"""The System health read model's pure parts (ADM-13 PR 2, #530).

Grouping status rows into processes (a restart's old row never shows as not
running, web processes are counted), the problems list, and the document
projection, without a database.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.source.data_age import Connection
from parishkit.stewardship.source.refresh_status import FullRefreshStatus
from parishkit.stewardship.system_health import (
    AcceptPreview,
    DebugOffPreview,
    DebugSwitchStatus,
    DropAcceptanceStatus,
    DropCount,
    Problem,
    RefreshAcceptPreview,
    Subject,
    SystemHealth,
    find_problems,
    group_processes,
)

NOW = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)


def row(service="web", process="main", *, age=0, started=600, **values):
    """A status row that last reported ``age`` seconds before NOW."""
    return {
        "service": service,
        "process": process,
        "target": None,
        "started_at": NOW - timedelta(seconds=started),
        "reported_at": NOW - timedelta(seconds=age),
        "application_version": "1.4.2",
        "debug_logging": False,
        "sender_state": "running" if service == "mail-dispatch" else None,
        "sender_since": None,
        "sender_until": None,
    } | values


def problems(processes=(), **values):
    """The problem codes for these processes and otherwise healthy inputs."""
    arguments = {
        "mode": "production",
        "processes": processes,
        "missing": (),
        "schema_current": True,
        "incidents": {},
        "refresh": None,
        "backup_at": None,
    } | values
    return [problem.kind for problem in find_problems(**arguments)]


def test_a_restart_replaces_the_old_row_instead_of_showing_it_stopped():
    """The newest-started row stands for its process; the old one is ignored."""
    lines = group_processes(
        [
            row("worker", age=900, started=4000),
            row("worker", age=10, started=300, application_version="1.4.3"),
        ],
        NOW,
    )
    assert len(lines) == 1
    assert lines[0].running and lines[0].version == "1.4.3"


def test_web_processes_are_counted_and_old_web_rows_ignored():
    """Several web workers share (web, main); only those reporting count."""
    lines = group_processes(
        [row(age=5), row(age=20, started=100), row(age=4000, started=9000)], NOW
    )
    assert [(line.service, line.live, line.running) for line in lines] == [
        ("web", 2, True)
    ]


def test_a_process_that_stopped_reporting_is_not_running():
    """Past three minutes without a report, the newest row shows not running."""
    (line,) = group_processes([row("scheduler", age=181)], NOW)
    assert not line.running
    assert problems((line,)) == ["not_running"]


def test_processes_follow_the_deployments_order():
    """Services and processes sort in the deployment's order, not by arrival."""
    lines = group_processes(
        [
            row("mail-dispatch", "mail"),
            row("worker", "source"),
            row("mail-dispatch"),
            row("web"),
            row("worker"),
        ],
        NOW,
    )
    assert [(line.service, line.process) for line in lines] == [
        ("web", "main"),
        ("worker", "main"),
        ("worker", "source"),
        ("mail-dispatch", "main"),
        ("mail-dispatch", "mail"),
    ]
    assert [line.label for line in lines] == [
        "Web portal",
        "Background worker",
        "ParishSoft refresh worker",
        "Mail sender 1",
        "Mail sender 2",
    ]


def test_debug_logging_is_a_problem_only_in_production():
    """In Testing the deployment tool turns it on on purpose."""
    lines = group_processes([row(debug_logging=True)], NOW)
    assert problems(lines) == ["debug_logging"]
    assert problems(lines, mode="testing") == []


def test_sender_states_that_are_problems():
    """Halted and outage-paused senders are problems; limits are reasons to wait."""
    lines = group_processes(
        [
            row("mail-dispatch", sender_state="gmail_held"),
            row("mail-dispatch", "mail", sender_state="halted"),
        ],
        NOW,
    )
    assert problems(lines) == ["sender_halted"]
    (outage,) = group_processes(
        [row("mail-dispatch", sender_state="outage_paused")], NOW
    )
    assert problems((outage,)) == ["sender_outage"]
    for state in ("running", "daily_limit", "gmail_held"):
        lines = group_processes([row("mail-dispatch", sender_state=state)], NOW)
        assert problems(lines) == [], state


def test_mixed_versions_missing_services_and_schema_are_problems():
    """Every reporting process's version must agree, and the schema must match."""
    lines = group_processes(
        [row("web"), row("worker", application_version="1.4.1")], NOW
    )
    assert problems(lines, missing=("scheduler",), schema_current=False) == [
        "not_reported",
        "versions_differ",
        "schema_mismatch",
    ]


def test_parishsoft_and_backup_problems():
    """A failing connection, a late held refresh, incidents and backups."""
    refresh = FullRefreshStatus(
        None,
        NOW - timedelta(hours=3),
        False,
        connection=Connection("failing", NOW - timedelta(hours=2)),
        overdue_at=NOW - timedelta(hours=1),
        out_of_date=True,
    )
    backup_at = NOW - timedelta(days=2)
    found = find_problems(
        mode="production",
        processes=(),
        missing=(),
        schema_current=True,
        incidents={
            "backup_rpo_breach": NOW - timedelta(hours=1),
            "source_destructive_change": NOW - timedelta(hours=4),
            "mail_provider_unavailable": NOW,
        },
        refresh=refresh,
        backup_at=backup_at,
    )
    assert [problem.kind for problem in found] == [
        "mail_provider_unavailable",
        "source_failing",
        "source_late",
        "source_destructive_change",
        "backup_rpo_breach",
    ]
    # The overdue backup's sentence names when the newest backup finished.
    assert found[-1].at == backup_at
    # A refresh held for a send is not late, and a failure without a failing
    # connection is named on its own.
    held = FullRefreshStatus(None, NOW, False, out_of_date=True, held_for_send=True)
    assert problems(refresh=held) == ["source_failed"]


def health(**values):
    """A healthy SystemHealth with ``values`` replaced."""
    return SystemHealth(
        **{
            "checked_at": NOW,
            "mode": "production",
            "problems": (),
            "processes": group_processes(
                [row(), row("mail-dispatch"), row("mail-dispatch", "mail")], NOW
            ),
            "missing_services": (),
            "versions": ("1.4.2",),
            "schema_current": True,
            "delivery_paused": False,
            "paused_at": None,
            "send_active": False,
            "planning_held": False,
            "retry_waiting": 0,
            "next_retry_at": None,
            "daily_sent": 0,
            "daily_limit": 1600,
            "daily_resume_at": None,
            "incidents": {},
            "refresh": FullRefreshStatus(NOW, None, False),
            "refused_at": None,
            "refused_counts": (),
            "backup_at": NOW,
            "backup_key_matches": True,
            "backup_bytes": 3 * 1024 * 1024,
            "backup_version": "1.4.2",
            "backup_request": None,
            "offsite": None,
            "debug_switch": None,
        }
        | values
    )


def test_the_document_holds_states_counts_and_instants_only():
    """No translated text, names or messages: the projection is plain values."""
    document = health(
        problems=(
            Problem("sender_halted", (Subject("mail-dispatch", "main"),), at=NOW),
        ),
        refused_at=NOW,
        refused_counts=(DropCount("family", 1084, 612, 25, True),),
        offsite=SimpleNamespace(
            kind="failed", message="Translated text", at=NOW, last_copy_at=None
        ),
    ).to_document()
    assert document["problems"] == [
        {
            "kind": "sender_halted",
            "subjects": [
                {"service": "mail-dispatch", "process": "main", "target": None}
            ],
            "at": NOW.isoformat(),
        }
    ]
    assert document["refused_counts"][0] == {
        "measure": "family",
        "before": 1084,
        "after": 612,
        "limit_percent": 25,
        "failed": True,
    }
    assert document["offsite"] == {
        "state": "failed",
        "at": NOW.isoformat(),
        "last_copy_at": None,
    }
    assert document["refresh"]["full_succeeded_at"] == NOW.isoformat()
    assert "Translated text" not in str(document)
    assert health().to_document()["offsite"] == {"state": "unset"}
    assert health().to_document()["debug_switch"] is None
    # The newest debug-off switch's times and who cleared it, never who set it.
    switch = DebugSwitchStatus(set_at=NOW, cleared_at=None, cleared_by=None)
    assert health(debug_switch=switch).to_document()["debug_switch"] == {
        "set_at": NOW.isoformat(),
        "cleared_at": None,
        "cleared_by": None,
    }


def test_nothing_waiting_and_everything_working_read_plainly():
    """A healthy system says so in the panel and the announcement."""
    healthy = health()
    assert not healthy.waiting
    assert healthy.announcement == "Everything is working."
    paused = health(
        delivery_paused=True,
        problems=(Problem("schema_mismatch"), Problem("versions_differ")),
    )
    assert paused.waiting
    assert paused.announcement == (
        "2 problems need attention: database does not match; versions differ."
    )
    # The sentence follows the set of problems, not only their number.
    swapped = health(
        problems=(
            Problem("schema_mismatch"),
            Problem("sender_halted", (Subject("mail-dispatch", "mail"),)),
        )
    )
    assert swapped.announcement == (
        "2 problems need attention: database does not match; Mail sender 2 halted."
    )


def test_the_status_region_states_problems_and_panels_in_words():
    """The fragment renders every problem sentence and panel without errors."""
    codes = (
        "sender_halted",
        "sender_outage",
        "mail_provider_unavailable",
        "not_running",
        "not_reported",
        "source_failing",
        "source_failed",
        "source_late",
        "source_destructive_change",
        "source_tenant_mismatch",
        "source_retention_failing",
        "backup_rpo_breach",
        "backup_offsite_failed",
        "backup_key_changed",
        "debug_logging",
        "versions_differ",
        "schema_mismatch",
    )
    shown = health(
        problems=tuple(
            Problem(code, (Subject("mail-dispatch", "main"),), at=NOW)
            if code.startswith("sender") or code in {"not_running", "debug_logging"}
            else Problem(
                code, (Subject("worker"),) if code == "not_reported" else (), at=NOW
            )
            for code in codes
        ),
        delivery_paused=True,
        paused_at=NOW,
        retry_waiting=3,
        next_retry_at=NOW,
        daily_sent=1612,
        refused_at=NOW,
        refused_counts=(
            DropCount("email_eligible_families", 1084, 612, 25, True),
            DropCount("fund", None, 12, 25, False),
        ),
    )
    body = render_to_string(
        "stewardship/system-health-status.html",
        {
            "health": shown,
            "campaign_id": None,
            "pause": {"actor": "admin@example.org", "reason": "Fixing a typo"},
            "poll_interval": 10000,
        },
    )
    assert body.count("data-problem=") == len(codes)
    assert "17 problems need attention" in body
    assert "Mail sender 1 has stopped all Family email" in body
    assert "The background worker has not reported its status" in body
    assert "Families with an email address" in body
    assert "Fell too far (more than 25%)" in body
    # "Before" is the week's largest full-refresh count (#387), and says so.
    assert "highest value over the full refreshes of the past week" in body
    assert "Paused by admin@example.org" in body
    assert "Reason: Fixing a typo" in body
    assert "3 Family emails wait to retry" in body
    # The 24-hour count is shown with the limit it is compared with (2c).
    assert (
        "Emails went to 1,612 recipients in the last 24 hours. Family "
        "invitations, reminders and Family tests stop at 1,600." in body
    )
    assert "?state=retry_wait" in body
    assert "All services run version 1.4.2." in body
    assert "stewardship-deployment-runbook.md" not in body  # no sender is stopped
    # How long ago the last backup finished is counted in the browser, so an
    # unchanged poll's markup stays the same.
    assert "data-live-since" in body and "Last checked" not in body
    # Internal names stay out of the words.
    assert "sender_halted<" not in body and "mail-dispatch<" not in body
    healthy = render_to_string(
        "stewardship/system-health-status.html",
        {"health": health(), "pause": None, "poll_interval": 10000},
    )
    assert "Everything is working" in healthy
    assert "Nothing is holding Family email back." in healthy
    # Shown even when nothing waits, so the Administrator sees the limit coming.
    assert "Emails went to 0 recipients in the last 24 hours." in healthy
    assert "Debug logging is off in every service that reports." in healthy
    one = render_to_string(
        "stewardship/system-health-status.html",
        {"health": health(daily_sent=1), "pause": None, "poll_interval": 10000},
    )
    assert "Emails went to 1 recipient in the last 24 hours." in one
    # Below the limit nothing waits for it, so no resume time is shown.
    assert "data-daily-resume" not in healthy and "data-daily-resume" not in one


def test_the_resume_time_shows_only_while_the_count_is_at_the_limit():
    """At the limit the count line says when sending resumes, as a local clock time."""
    resume = NOW + timedelta(hours=5, minutes=10)
    body = render_to_string(
        "stewardship/system-health-status.html",
        {
            "health": health(daily_sent=1612, daily_resume_at=resume),
            "pause": None,
            "poll_interval": 10000,
        },
    )
    line = body.split("data-daily-count>", 1)[1].split("</p>", 1)[0]
    assert "Sending resumes at about" in line
    # The browser shows it in its own time zone as a clock time (it is always
    # within the next 24 hours); the server never labels it with a zone.
    stamp = resume.isoformat()
    assert (
        f'<time datetime="{stamp}" data-local-instant data-time-only data-time-day>'
        in line
    )
    assert "UTC" not in line


def test_the_daily_limit_shown_is_where_family_mail_stops():
    """The page's limit is the mail sender's: at it Family mail stops, below not."""
    from parishkit.stewardship.jobs.family_mail_dispatch import over_daily_limit

    limit = health().daily_limit
    assert over_daily_limit("initial", limit) is True
    assert over_daily_limit("initial", limit - 1) is False


def test_the_resume_function_uses_the_family_limit():
    """0076 fixes the Family limit in its body; it must be the mail sender's."""
    from pathlib import Path

    from parishkit.stewardship.jobs import family_mail_dispatch as dispatch

    sql = (
        Path(dispatch.__file__).resolve().parents[1]
        / "schema/migrations/0076_daily_send_resume.sql"
    ).read_text(encoding="utf-8")
    limit = dispatch.DAILY_SEND_LIMIT - dispatch.RESERVED_SENDS
    assert sql.count(f"<{limit}") == 2 and sql.count(f">={limit}") == 1
    assert limit == health().daily_limit


def test_a_stopped_mail_sender_links_the_deployment_runbook():
    """Only the server operator can start it, so the line says where to look."""
    stopped = health(
        processes=group_processes(
            [row(), row("mail-dispatch", age=600), row("mail-dispatch", "mail")], NOW
        )
    )
    body = render_to_string(
        "stewardship/system-health-status.html",
        {"health": stopped, "pause": None, "poll_interval": 10000},
    )
    assert "Not running." in body
    assert (
        'href="https://github.com/epiphany40223/parishkit/blob/main/docs/guides/'
        'stewardship-deployment-runbook.md"' in body
    )


def test_an_unreadable_configured_backup_key_is_unavailable(monkeypatch):
    """A damaged applied key is a configuration error (503), not a bad request."""
    import pytest

    from parishkit.config import ConfigError
    from parishkit.stewardship import system_health
    from parishkit.stewardship.accounts import backup_key
    from parishkit.stewardship.jobs.backup_models import BackupRun

    newest = SimpleNamespace(first=lambda: (NOW, "0123456789abcdef", 1, 2, "1.4.2"))
    monkeypatch.setattr(
        BackupRun,
        "objects",
        SimpleNamespace(
            order_by=lambda *names: SimpleNamespace(values_list=lambda *f: newest)
        ),
    )
    monkeypatch.setattr(
        backup_key,
        "configured_key",
        lambda configuration: {"values": {"settings": {"public_key": "not a key"}}},
    )
    with pytest.raises(ConfigError):
        system_health._backup(object())


def test_a_condition_in_several_services_is_one_problem():
    """Debug logging, stopped and silent services and halts are listed once (#686).

    Each name keeps its own spelling wherever it falls in the list: the
    ParishSoft refresh worker is not first here.
    """
    rows = [
        row("web", debug_logging=True),
        row("worker", age=450),
        row("worker", "source", age=400),
        row("scheduler", debug_logging=True),
        row("mail-dispatch", debug_logging=True, sender_state="halted"),
        row("mail-dispatch", "mail", debug_logging=True, sender_state="halted"),
        row("config-installer", age=500),
    ]
    found = find_problems(
        mode="production",
        processes=group_processes(rows, NOW),
        missing=(),
        schema_current=True,
        incidents={},
        refresh=None,
        backup_at=None,
    )
    assert [problem.kind for problem in found] == [
        "sender_halted",
        "not_running",
        "debug_logging",
    ]
    halted, stopped, debug = found
    assert halted.names == "Both mail senders"
    assert stopped.names == (
        "The background worker, the ParishSoft refresh worker and the settings "
        "installer"
    )
    # The most recent report among the stopped processes.
    assert stopped.at == NOW - timedelta(seconds=400)
    assert debug.names_inline == "the web portal, the scheduler and both mail senders"
    body = render_to_string(
        "stewardship/system-health-status.html",
        {"health": health(problems=found), "pause": None, "poll_interval": 10000},
    )
    assert body.count('data-problem="debug_logging"') == 1
    assert (
        "Debug logging is on in Production in the web portal, the scheduler and "
        "both mail senders."
    ) in body
    assert "Both mail senders have stopped all Family email" in body
    assert (
        "The background worker, the ParishSoft refresh worker and the settings "
        "installer have not reported for more than three minutes"
    ) in body
    assert health(problems=found).announcement == (
        "3 problems need attention: Both mail senders halted; The background "
        "worker, the ParishSoft refresh worker and the settings installer not "
        "running; debug logging on in the web portal, the scheduler and both "
        "mail senders."
    )


def test_one_process_reads_as_its_own_phrase():
    """A single mail sender with debug logging on reads "in mail sender 1"."""
    (debug,) = find_problems(
        mode="production",
        processes=group_processes([row("mail-dispatch", debug_logging=True)], NOW),
        missing=(),
        schema_current=True,
        incidents={},
        refresh=None,
        backup_at=None,
    )
    body = render_to_string(
        "stewardship/system-health-status.html",
        {"health": health(problems=(debug,)), "pause": None, "poll_interval": 10000},
    )
    assert "Debug logging is on in Production in mail sender 1." in body
    assert "the mail sender 1" not in body


def test_a_requested_backup_reads_as_the_page_words_it():
    """Request mode's lapse rules apply on the page before the next poll."""
    from parishkit.stewardship.system_health import (
        BackupRequestStatus,
        request_status,
    )

    def status(state, *, age=0, held=None, claimed=None, done=None, kind=None):
        """The status of a request made ``age`` minutes before NOW."""

        def ago(minutes):
            """An instant ``minutes`` before NOW, or None."""
            return None if minutes is None else NOW - timedelta(minutes=minutes)

        return request_status(
            {
                "state": state,
                "created_at": ago(age),
                "held_at": ago(held),
                "claimed_at": ago(claimed),
                "finished_at": ago(done),
                "failure_kind": kind,
            },
            NOW,
        )

    assert status("waiting", age=5) == "waiting"
    assert status("waiting", age=40, held=2) == "held"
    assert status("waiting", age=31) == "not_picked_up"
    assert status("waiting", age=90, held=35) == "not_picked_up"
    assert status("running", age=10, claimed=9) == "running"
    assert status("running", age=200, claimed=121) == "did_not_finish"
    for state in ("finished", "failed"):
        assert status(state, age=5, claimed=4, done=1) == state
    assert status("failed", age=300, kind="did_not_finish") == "did_not_finish"
    # Expired by request mode after its 30 minutes, or by a restore (as of
    # its own creation).
    assert status("expired", age=40, done=5) == "not_picked_up"
    assert status("expired", age=40, done=40) == "restored"
    for code, words in (
        ("waiting", "is waiting for the server"),
        ("held", "was held while a Family email send was in progress"),
        ("not_picked_up", "The server did not pick up the backup requested at"),
        ("running", "is running; it started at"),
        ("did_not_finish", "did not finish."),
        ("failed", "failed. Ask the server operator"),
        ("finished", "finished at"),
        ("restored", "was never run: the system was restored"),
    ):
        request = BackupRequestStatus(
            state="waiting",
            status=code,
            created_at=NOW,
            held_at=None,
            claimed_at=NOW,
            finished_at=NOW,
            failure_kind=None,
        )
        body = render_to_string(
            "stewardship/system-health-status.html",
            {
                "health": health(backup_request=request),
                "pause": None,
                "poll_interval": 10000,
            },
        )
        assert words in body, code
        assert f'data-backup-request="{code}"' in body
    body = render_to_string(
        "stewardship/system-health-status.html",
        {"health": health(), "pause": None, "poll_interval": 10000},
    )
    assert "Its size is 3.0" in body and "taken by version 1.4.2" in body


def debug_page(*, switch=None, debug=True, actor="admin@example.org", **values):
    """Render the status region and the Turn off debug logging region together.

    ``switch`` is the newest debug-off switch; ``debug`` whether the web
    portal reports debug logging in effect. ``values`` are the view's
    other context (a step's ``debug_off`` result).
    """
    processes = group_processes([row(debug_logging=debug)], NOW)
    context = {
        "health": health(processes=processes, debug_switch=switch),
        "pause": None,
        "debug_actor": actor if switch is not None and switch.active else None,
        "poll_interval": 10000,
    } | values
    return render_to_string(
        "stewardship/system-health-status.html", context
    ) + render_to_string("stewardship/system-health-debug-off.html", context)


def test_turn_off_debug_logging_is_offered_while_a_service_has_it_on():
    """The button posts the preview step in place; help says who clears it."""
    body = debug_page()
    assert 'value="debug_off_preview"' in body
    assert 'data-in-place="debug-off-preview"' in body
    assert 'action="/admin/system/health/#debug-off"' in body
    # The polled copy carries the same enabled button for the mirror.
    assert '<template data-live-mirror="debug-off-button">' in body
    assert "pk-stewardship debug-off-clear" in body
    assert "those whose server setting is still on log in debug form again" in body
    assert "Use Turn off debug logging on this page" in body


def test_a_set_switch_greys_the_button_and_says_who_and_when():
    """Who set it and when (browser-local), and that the setting still says on."""
    switch = DebugSwitchStatus(set_at=NOW, cleared_at=None, cleared_by=None)
    body = debug_page(switch=switch)
    assert 'value="debug_off_preview"' not in body
    assert 'disabled aria-describedby="debug-off-reason"' in body
    assert "Debug logging was turned off by admin@example.org at" in body
    assert f'<time datetime="{NOW.isoformat()}" data-local-instant>' in body
    assert 'data-debug-switch="set"' in body
    assert "The setting on the server still says “on”." in body
    # Once no service reports debug logging, the button stays greyed with why.
    quiet = debug_page(switch=switch, debug=False)
    assert "Debug logging is off in every service that reports." in quiet
    assert 'data-debug-switch="set"' in quiet
    unset = debug_page(debug=False)
    assert 'value="debug_off_preview"' not in unset
    assert 'disabled aria-describedby="debug-off-reason"' in unset


def test_a_cleared_switch_is_named_only_while_debug_logging_is_back():
    """The operator's clear is stated while it matters, not forever."""
    cleared = DebugSwitchStatus(
        set_at=NOW - timedelta(hours=2), cleared_at=NOW, cleared_by="operator"
    )
    body = debug_page(switch=cleared)
    assert "The server operator cleared the debug-off switch at" in body
    assert 'value="debug_off_preview"' in body
    assert "cleared the debug-off switch" not in debug_page(switch=cleared, debug=False)


def test_the_preview_and_each_refusal_read_in_the_region():
    """Preview, result and refusals are drawn inside #debug-off."""
    preview = DebugOffPreview(key="k-1", processes=("the web portal",))
    body = debug_page(debug_off={"preview": preview})
    for words in (
        "Debug logging is on in: the web portal.",
        "Lines already written stay in the logs",
        "still says “on” until the server operator recreates the services",
        "Only the server operator can clear the switch.",
        "Confirm: turn off debug logging",
    ):
        assert words in body, words
    assert 'name="key" value="k-1"' in body
    assert 'value="debug_off_confirm"' in body and 'value="debug_off_cancel"' in body
    # The preview replaces the region's button, so a poll cannot discard it.
    region = body[body.index('id="debug-off"') :]
    assert "data-live-mirror-target" not in region
    done = debug_page(
        switch=DebugSwitchStatus(set_at=NOW, cleared_at=None, cleared_by=None),
        debug_off={"requested": SimpleNamespace(set_at=NOW), "created": True},
    )
    assert "data-debug-off-set" in done and "Debug logging turned off at" in done
    for refused, words in (
        ("reauthenticate", "For your security, confirm it's you with Google"),
        ("busy", "Debug logging has already been turned off"),
        ("denied", "The server refused to turn off debug logging"),
        ("invalid", "That request could not be read."),
    ):
        answer = debug_page(debug_off={"refused": refused})
        assert f'data-debug-off-refused="{refused}"' in answer
        assert words in answer, refused


# ------------------------------------------------- accept this change once

FAILED = (
    DropCount("family", 1084, 612, 25, True),
    DropCount("member", 3000, 2900, 25, False),
    DropCount("email_eligible_families", 900, 500, 25, True),
)
ACCEPTANCE = UUID("00000000-0000-4000-8000-0000000000a5")


def accepted(*, live=True):
    """An acceptance of the refusal shown, given at NOW."""
    return DropAcceptanceStatus(
        id=ACCEPTANCE, created_at=NOW, expires_at=NOW + timedelta(hours=24), live=live
    )


def accept_page(
    counts=FAILED, acceptance=None, actor="admin@example.org", request=None, **values
):
    """Render the status region, which holds the Accept this change once
    region under the refusal.

    ``values`` are the view's other context (a step's ``accept`` result, the
    page's ``status_url``).
    """
    context = {
        "health": health(
            refused_at=NOW, refused_counts=counts, drop_acceptance=acceptance
        ),
        "pause": None,
        "accept_actor": actor if acceptance is not None and acceptance.live else None,
        "poll_interval": 10000,
    } | values
    return render_to_string(
        "stewardship/system-health-status.html", context, request=request
    )


def test_which_refusals_can_be_accepted_once():
    """Open only for a refusal with a failing count, no acceptance yet and
    Families and Members in the load; otherwise the reason it cannot be."""
    assert health().accept_state == "none"
    assert health(refused_counts=FAILED).accept_state == "open"
    assert health(refused_counts=FAILED, drop_acceptance=accepted()).accept_state == (
        "accepted"
    )
    lapsed = health(refused_counts=FAILED, drop_acceptance=accepted(live=False))
    assert lapsed.accept_state == "lapsed"
    empty = (DropCount("family", 1084, 0, 25, True),)
    assert health(refused_counts=empty).accept_state == "empty"
    no_members = (*FAILED[:1], DropCount("member", 3000, 0, 25, True))
    assert health(refused_counts=no_members).accept_state == "empty"


def test_how_far_a_count_fell_is_rounded_up():
    """Rounded up, so a count past the limit never reads as the limit."""
    assert DropCount("family", 1000, 749, 25, True).fell_percent == 26
    assert DropCount("family", 1084, 612, 25, True).fell_percent == 44
    assert DropCount("family", 1000, 1000, 25, False).fell_percent is None
    assert DropCount("family", 1000, 1200, 25, False).fell_percent is None
    assert DropCount("family", None, 10, 25, False).fell_percent is None
    assert DropCount("family", 0, 0, 25, True).fell_percent is None


def test_the_acceptance_document_never_names_who_gave_it():
    """The id, times and whether it is live; who accepted is the page's."""
    document = health(refused_counts=FAILED, drop_acceptance=accepted()).to_document()
    assert document["drop_acceptance"] == {
        "id": str(ACCEPTANCE),
        "created_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(hours=24)).isoformat(),
        "live": True,
    }
    assert health().to_document()["drop_acceptance"] is None


def test_the_preview_document_holds_counts_and_the_binding_only():
    """``system refresh-accept-preview`` prints counts, never names."""
    attempt = uuid4()
    document = RefreshAcceptPreview(
        attempt_id=attempt,
        refused_at=NOW.isoformat(),
        counts=[
            {
                "measure": "family",
                "before": 1084,
                "after": 612,
                "limit_percent": 25,
                "failed": True,
            }
        ],
        preview={"token": "signed"},
    ).to_document()
    assert set(document) == {"attempt_id", "refused_at", "counts", "preview"}
    assert document["attempt_id"] == str(attempt)
    assert set(document["counts"][0]) == {
        "measure",
        "before",
        "after",
        "limit_percent",
        "failed",
    }


def test_accept_this_change_once_is_offered_for_an_open_refusal():
    """The button posts the preview step in place; help is in a toggletip."""
    body = accept_page()
    assert 'value="accept_preview"' in body
    assert 'data-in-place="accept-preview"' in body
    assert 'action="/admin/system/health/#accept-change"' in body
    assert '<template data-live-mirror="accept-button">' in body
    assert 'data-live-mirror-target="accept-button"' in body
    assert 'aria-controls="accept-change-intro-tip"' in body
    assert "accepted once with Accept this change once just below" in body
    assert "reload System health" not in body
    # The emergency path stays the operator's, named in the help.
    assert "the server operator can still let a change through" in body


def test_the_accept_region_sits_right_under_the_refusal():
    """In the ParishSoft refresh panel, directly after the refused change's
    notice, inside the element the poll keeps (data-live-keep); its heading
    is a subheading of the panel's. The polled copy carries no CSRF token,
    so polls compare equal; the page's own copy does."""
    import re

    from django.test import RequestFactory

    def kept(body):
        """The kept element's markup, up to the end of its region."""
        start = body.index('<div data-live-keep="accept-change">')
        return body[start : body.index("</section>", start)]

    request = RequestFactory().get("/admin/system/health/")
    body = accept_page(request=request, status_url="/admin/system/health/status/")
    panel = body[body.index('aria-labelledby="health-source-heading"') :]
    panel = panel[: panel.index("</section>\n\n")]
    # The refusal notice's last line, then at once the kept element.
    before, after = panel.split('<div data-live-keep="accept-change">', 1)
    assert before.rstrip().endswith("every later refresh is refused too.</p>\n</div>")
    assert "data-refused-change" in before
    assert after.startswith('<section class="panel" id="accept-change"')
    assert '<h3 id="accept-change-heading">Accept this change once</h3>' in after
    assert body.count('id="accept-change"') == 1
    token = r'name="csrfmiddlewaretoken" value="([^"]*)"'
    assert re.search(token, kept(body)).group(1)
    polled = kept(accept_page(request=request))
    assert re.search(token, polled).group(1) == ""
    # Nothing is drawn while no refusal is shown, but the kept element is.
    empty = accept_page(counts=())
    assert '<div data-live-keep="accept-change"></div>' in empty
    assert 'id="accept-change"' not in empty


def test_an_acceptance_greys_the_button_with_who_when_and_until_when():
    """Who accepted, when and when it expires, in browser-local time, and
    that the next full refresh will use it."""
    body = accept_page(acceptance=accepted())
    assert 'value="accept_preview"' not in body
    assert 'disabled aria-describedby="accept-change-reason"' in body
    assert 'data-accept-state="accepted"' in body
    assert "Accepted by admin@example.org at" in body
    assert f'<time datetime="{NOW.isoformat()}" data-local-instant>' in body
    expires = (NOW + timedelta(hours=24)).isoformat()
    assert f'<time datetime="{expires}" data-local-instant>' in body
    assert "The next full refresh will use it" in body


def test_a_lapsed_acceptance_offers_refresh_now():
    """A refusal is accepted once; a lapsed one points to Refresh now."""
    body = accept_page(acceptance=accepted(live=False), actor=None)
    assert 'data-accept-state="lapsed"' in body
    assert "the acceptance ended before a full refresh used it" in body
    reason = body[body.index('id="accept-change-reason"') :]
    assert 'href="/admin/parish/parishsoft-refresh/"' in reason
    assert "Accepted by" not in body


def test_an_empty_load_says_it_can_never_be_accepted():
    """Refused at any setting, by design, so the button stays greyed."""
    body = accept_page(counts=(DropCount("family", 1084, 0, 25, True),))
    assert 'data-accept-state="empty"' in body
    assert "can never be accepted" in body
    assert 'value="accept_preview"' not in body


def test_the_preview_lists_the_counts_and_waits_for_the_tick_box():
    """Every count with before, after and how far it fell, the failing ones
    marked; Confirm waits for "I checked this in ParishSoft"."""
    preview = AcceptPreview(
        token="signed-binding", attempt_id=uuid4(), refused_at=NOW, counts=FAILED
    )
    body = accept_page(accept={"preview": preview})
    region = body[body.index('id="accept-change"') :]
    assert region.count("data-accept-failed") == 2
    for words in (
        "Families with an email address",
        "1,084",
        "612",
        "44%",
        "Fell too far (more than 25%)",
        "Within the limit",
        "Records missing from ParishSoft leave the campaign",
        "Confirming also asks for a full refresh now.",
        "I checked this in ParishSoft, and the change is real.",
        "Confirm: accept this change once",
    ):
        assert words in region, words
    assert 'name="token" value="signed-binding"' in region
    assert 'name="checked" value="yes" required data-acknowledgment' in region
    assert 'value="accept_confirm"' in region and 'value="accept_cancel"' in region
    assert 'aria-controls="accept-change-rules-tip"' in region
    # The preview replaces the region's button, so a poll cannot discard it.
    assert "data-live-mirror-target" not in region


@pytest.mark.parametrize(
    ("refused", "marker", "words"),
    [
        ("reauthenticate", "reauthenticate", "For your security, confirm it's you"),
        ("busy", "changed", "This changed since you looked; review it again."),
        ("denied", "denied", "The server refused this acceptance"),
        ("invalid", "invalid", "or the box was not ticked"),
    ],
)
def test_each_refusal_reads_in_the_region(refused, marker, words):
    """Never the database's words: "no longer the newest" is "changed"."""
    body = accept_page(accept={"refused": refused})
    region = body[body.index('id="accept-change"') :]
    assert f'data-accept-refused="{marker}"' in region
    assert words in region
    assert "no longer the newest" not in region


def test_the_confirmation_says_a_full_refresh_was_requested():
    """Or, when it could not be, that the next full refresh uses it."""
    done = accept_page(
        acceptance=accepted(),
        accept={"requested": SimpleNamespace(), "created": True, "refresh": uuid4()},
    )
    assert "data-accept-done" in done and "A full refresh was requested" in done
    assert "Accepted by admin@example.org at" in done
    missed = accept_page(
        acceptance=accepted(),
        accept={"requested": SimpleNamespace(), "created": True, "refresh": None},
    )
    assert "A full refresh could not be requested right now" in missed
    assert "the acceptance stands" in missed
    assert "Nothing was accepted" not in missed


def test_the_binding_is_the_reviewers_own_and_unaltered(settings, monkeypatch):
    """An altered binding or another Administrator's is invalid; an expired
    one is "changed" (review it again)."""
    from django.core import signing

    from parishkit.stewardship import system_health
    from parishkit.stewardship.storage import StaleRecordError

    actor = SimpleNamespace(identity=uuid4())
    key, attempt = uuid4(), uuid4()
    token = system_health._sign(actor, key, attempt, FAILED)
    found = system_health._unsign(actor, token)
    assert found[:2] == (key, attempt)
    assert found[2]["family"] == {"before": 1084, "after": 612, "failed": True}
    with pytest.raises(ValueError):
        system_health._unsign(actor, token + "x")
    with pytest.raises(ValueError):
        system_health._unsign(SimpleNamespace(identity=uuid4()), token)
    with pytest.raises(ValueError):
        system_health._unsign(actor, signing.dumps({"key": "x"}, salt="other"))
    monkeypatch.setattr(system_health, "ACCEPT_MAX_AGE", -1)
    with pytest.raises(StaleRecordError):
        system_health._unsign(actor, token)


def test_the_binding_lasts_the_shared_preview_lifetime():
    """Fifteen minutes, as every configuration-change preview."""
    from parishkit.stewardship import system_health
    from parishkit.stewardship.web.refusals import PREVIEW_MAX_AGE

    assert system_health.ACCEPT_MAX_AGE == PREVIEW_MAX_AGE == 15 * 60
