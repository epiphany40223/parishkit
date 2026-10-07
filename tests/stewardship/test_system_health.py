"""The System health read model's pure parts (ADM-13 PR 2, #530).

Grouping status rows into processes (a restart's old row never shows as not
running, web processes are counted), the problems list, and the document
projection, without a database.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from django.template.loader import render_to_string

from parishkit.stewardship.source.data_age import Connection
from parishkit.stewardship.source.refresh_status import FullRefreshStatus
from parishkit.stewardship.system_health import (
    DropCount,
    Problem,
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
    return [problem.code for problem in find_problems(**arguments)]


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
    assert [problem.code for problem in found] == [
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
            "incidents": {},
            "refresh": FullRefreshStatus(NOW, None, False),
            "refused_at": None,
            "refused_counts": (),
            "backup_at": NOW,
            "backup_key_matches": True,
            "offsite": None,
        }
        | values
    )


def test_the_document_holds_states_counts_and_instants_only():
    """No translated text, names or messages: the projection is plain values."""
    document = health(
        problems=(Problem("sender_halted", "mail-dispatch", "main", at=NOW),),
        refused_at=NOW,
        refused_counts=(DropCount("family", 1084, 612, 25, True),),
        offsite=SimpleNamespace(
            kind="failed", message="Translated text", at=NOW, last_copy_at=None
        ),
    ).to_document()
    assert document["problems"] == [
        {
            "code": "sender_halted",
            "service": "mail-dispatch",
            "process": "main",
            "target": None,
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
            Problem("sender_halted", "mail-dispatch", "mail"),
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
            Problem(code, "mail-dispatch", "main", at=NOW)
            if code.startswith("sender") or code in {"not_running", "debug_logging"}
            else Problem(code, "worker" if code == "not_reported" else None, at=NOW)
            for code in codes
        ),
        delivery_paused=True,
        paused_at=NOW,
        retry_waiting=3,
        next_retry_at=NOW,
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
    assert "Background worker has not reported its status" in body
    assert "Families with an email address" in body
    assert "Fell too far (more than 25%)" in body
    assert "Paused by admin@example.org" in body
    assert "Reason: Fixing a typo" in body
    assert "3 Family emails wait to retry" in body
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
    assert "Debug logging is off in every service that reports." in healthy


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

    newest = SimpleNamespace(first=lambda: (NOW, "0123456789abcdef"))
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
