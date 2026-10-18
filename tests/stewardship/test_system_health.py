"""The System health read model's pure parts (ADM-13 PR 2, #530).

Grouping status rows into processes (a restart's old row never shows as not
running, web processes are counted), the problems list, and the document
projection, without a database.
"""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.source.data_age import Connection
from parishkit.stewardship.source.refresh_status import FullRefreshStatus
from parishkit.stewardship.system_health import (
    BACKUP_SUMMARIES,
    DEBUG_SUMMARIES,
    INCIDENTS,
    LOADED_TILES,
    MAIL_SUMMARIES,
    REGISTRY,
    RESAVE_SUMMARIES,
    SOURCE_FORM_SUMMARIES,
    SOURCE_SUMMARIES,
    TILES,
    VERSION_SUMMARIES,
    AcceptPreview,
    BackupPreview,
    BackupRequestStatus,
    DebugAllowPreview,
    DebugOffPreview,
    DebugSwitchStatus,
    DropAcceptanceStatus,
    DropCount,
    Problem,
    RefreshAcceptPreview,
    Subject,
    SystemHealth,
    Unrecorded,
    dashboard,
    find_problems,
    group_processes,
    loaded_query,
    loaded_states,
    parse_loaded,
    resave_tile,
    source_form_tile,
    tile_parts,
)

NOW = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
TEMPLATES = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/templates/stewardship"
)


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


def page(context, *, request=None):
    """The page's dashboard as the view draws it, without the Admin chrome.

    Returns ``(region, tiles)``: the polled status region (the overall line
    and the copies the poll carries), then the seven tiles with their
    details. ``context`` is the view's; its dashboard is built from
    ``health`` as the view builds it, unless given.
    """
    context = {"pause": None, "poll_interval": 10000} | context
    model = context["health"]
    context.setdefault("dashboard", dashboard(model))
    region = render_to_string(
        "stewardship/system-health-status.html", context, request=request
    )
    tiles = "".join(
        render_to_string(
            "stewardship/system-health-tile.html", context | {"tile": tile}, request
        )
        for tile in context["dashboard"].tiles
    )
    return region, tiles


def whole(context, *, request=None):
    """The region and the tiles as one string."""
    return "".join(page(context, request=request))


def section(body, marker):
    """``body`` from ``marker`` (such as 'id="accept-change"') to the end of
    the section it opens."""
    start = body.index(marker)
    return body[start : body.index("</section>", start)]


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
            "severity": "error",
            "tiles": ["mail"],
            "subjects": [
                {"service": "mail-dispatch", "process": "main", "target": None}
            ],
            "at": NOW.isoformat(),
            "count": 0,
            "message_id": None,
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


def test_every_problem_kind_has_one_home_tile_and_a_severity():
    """The registry covers every kind find_problems can return (#1214)."""
    kinds = {
        "sender_halted",
        "sender_outage",
        "refusal_unrecorded",
        "not_running",
        "not_reported",
        "source_failing",
        "source_failed",
        "source_late",
        "debug_logging",
        "versions_differ",
        "schema_mismatch",
        "workgroup_missing",
        "backup_key_mismatch",
        "backup_key_unchecked",
        "backup_request_failed",
        "offsite_missing",
        *INCIDENTS,
    }
    assert set(REGISTRY) == kinds
    for tile, severity in REGISTRY.values():
        assert tile in TILES and tile not in LOADED_TILES
        assert severity in {"warning", "error"}
    # The Administrator's severity map (decisions 4 and 11, 2026-10-13).
    assert REGISTRY["source_failed"] == ("source", "warning")
    assert REGISTRY["source_late"] == ("source", "warning")
    assert REGISTRY["debug_logging"] == ("debug", "warning")
    assert REGISTRY["backup_rpo_breach"] == ("backups", "error")
    assert REGISTRY["refusal_unrecorded"] == ("mail", "error")


def test_a_process_problem_is_split_between_mail_and_version():
    """Mail sender subjects go to Mail, the rest to Version and services,
    each part naming only its own subjects and its own last report; a part
    of installers alone is a warning. The problem itself stays one."""
    lines = group_processes(
        [
            row("web", age=900),
            row("mail-dispatch", age=600),
            row("config-installer", age=1200),
        ],
        NOW,
    )
    (problem,) = find_problems(
        mode="production",
        processes=lines,
        missing=(),
        schema_current=True,
        incidents={},
        refresh=None,
        backup_at=None,
    )
    assert problem.kind == "not_running"
    assert problem.tiles == ("mail", "version")
    assert problem.severity == "error"
    (mail, mail_severity, mail_part), (version, severity, part) = tile_parts(
        problem, lines
    )
    assert (mail, mail_severity) == ("mail", "error")
    assert mail_part.subjects == (Subject("mail-dispatch", "main"),)
    assert mail_part.at == NOW - timedelta(seconds=600)
    assert (version, severity) == ("version", "error")
    assert part.names == "The web portal and the settings installer"
    assert part.at == NOW - timedelta(seconds=900)
    installer = Problem("not_running", (Subject("config-installer", "main"),))
    assert tile_parts(installer) == (("version", "warning", installer),)
    assert installer.severity == "warning"
    # Unsplit, the group's own time stays.
    assert tile_parts(problem)[0][2].at == problem.at


def _tiles(**values):
    """The dashboard's tiles for a SystemHealth with ``values``, by key."""
    found = dashboard(health(**values))
    return found, {tile.key: tile for tile in found.tiles}


def test_a_healthy_system_is_all_working_in_seven_tiles():
    """Every tile is drawn, in the fixed order, closed and Working."""
    found, tiles = _tiles()
    assert tuple(tiles) == TILES
    assert found.state == "working" and found.icon == "ok"
    assert all(tile.state == "working" and not tile.open for tile in found.tiles)
    assert found.announcement == "System health: all working."
    assert [tile.name for tile in found.tiles] == [
        "Mail",
        "ParishSoft refresh",
        "Backups",
        "Debug logging",
        "Families the form cannot open",
        "Pages and emails to re-save",
        "Version and services",
    ]


def test_tiles_take_their_worst_problem_and_the_line_counts_tiles():
    """Two problems in one tile count once, under the worse; the summary
    names the most severe and how many more there are."""
    found, tiles = _tiles(
        problems=(
            Problem("sender_outage", (Subject("mail-dispatch", "main"),), at=NOW),
            Problem("backup_offsite_failed", at=NOW),
            Problem("backup_rpo_breach", at=NOW),
            Problem("schema_mismatch"),
        )
    )
    assert found.state == "error" and found.icon == "error"
    assert [tile.key for tile in found.errors] == ["backups", "version"]
    assert [tile.key for tile in found.warnings] == ["mail"]
    backups = tiles["backups"]
    assert backups.open and backups.icon == "error"
    assert backups.top.problem.kind == "backup_rpo_breach"
    assert backups.more == 1
    assert tiles["mail"].top.severity == "warning" and tiles["mail"].more == 0
    assert found.announcement == (
        "System health: 2 errors (Backups, Version and services) and 1 warning (Mail)."
    )
    alone = dashboard(health(problems=(Problem("debug_logging", (Subject("web"),)),)))
    assert alone.announcement == "System health: 1 warning (Debug logging)."


def test_an_accepted_refusal_is_shown_in_its_tile_but_not_counted():
    """The Administrator handled it; the tile lists it and stays Working."""
    accepted = DropAcceptanceStatus(
        id=UUID(int=5), created_at=NOW, expires_at=NOW + timedelta(hours=24), live=True
    )
    found, tiles = _tiles(
        problems=(Problem("source_destructive_change", at=NOW),),
        refused_at=NOW,
        refused_counts=(DropCount("email_eligible_families", 1084, 612, 25, True),),
        drop_acceptance=accepted,
    )
    source = tiles["source"]
    assert source.state == "working" and len(source.problems) == 1
    assert source.counted == () and source.top is None
    assert found.state == "working"


def failed_request(status="not_picked_up", at=NOW):
    """A Take a backup now request that ended without a backup."""
    return SimpleNamespace(status=status, created_at=at)


def test_the_new_shared_kinds_and_where_they_are_listed():
    """The four former page notices are shared problems (#1214, slice 1):
    after the other ParishSoft and backup problems, in the registry's
    order, each raising its tile; off-site copies only in Production."""
    found = find_problems(
        mode="production",
        processes=(),
        missing=(),
        schema_current=True,
        incidents={
            "source_retention_failing": NOW,
            "backup_rpo_breach": NOW,
            "backup_offsite_failed": NOW,
        },
        refresh=None,
        backup_at=NOW,
        workgroup_missing="No reminders",
        backup_key="mismatch",
        backup_request=failed_request(),
        offsite_set_up=False,
    )
    assert [problem.kind for problem in found] == [
        "source_retention_failing",
        "workgroup_missing",
        "backup_rpo_breach",
        "backup_offsite_failed",
        "backup_key_mismatch",
        "backup_request_failed",
        "offsite_missing",
    ]
    workgroup, request = found[1], found[5]
    assert (workgroup.workgroup, workgroup.detail) == ("No reminders", None)
    assert (request.at, request.detail) == (NOW, "not_picked_up")
    assert {problem.severity for problem in found[4:]} == {"warning"}
    tiles = {tile.key: tile for tile in dashboard(health(problems=found)).tiles}
    assert tiles["source"].state == "warning" and tiles["source"].more == 1
    assert tiles["backups"].state == "error" and tiles["backups"].more == 4
    # The words a tile shows when one of them leads it.
    words = {problem.kind: problem.summary for problem in found}
    assert (
        words["workgroup_missing"] == "No-reminders WorkGroup not found in ParishSoft"
    )
    assert words["backup_key_mismatch"] == "last backup sealed to a different key"
    assert words["backup_request_failed"] == "requested backup did not complete"
    assert words["offsite_missing"] == "off-site copies not set up"
    # Off-site copies not set up are a problem in Production only, and a
    # request that is live or finished is none.
    assert problems(mode="testing", offsite_set_up=False) == []
    assert problems(offsite_set_up=False) == ["offsite_missing"]
    for status in ("waiting", "held", "running", "finished", "restored"):
        assert problems(backup_request=failed_request(status)) == [], status
    for status in ("failed", "did_not_finish", "not_picked_up"):
        assert problems(backup_request=failed_request(status)) == [
            "backup_request_failed"
        ], status


def test_an_unknown_workgroup_is_an_error_while_reminders_are_scheduled():
    """#861: the next Reminder would reach Families meant to be skipped."""
    (scheduled,) = find_problems(
        mode="production",
        processes=(),
        missing=(),
        schema_current=True,
        incidents={},
        refresh=None,
        backup_at=NOW,
        workgroup_missing="No reminders",
        reminders_scheduled=True,
    )
    assert scheduled.detail == "reminders_scheduled"
    assert scheduled.severity == "error" and scheduled.tiles == ("source",)
    (_, source, *_) = dashboard(health(problems=(scheduled,))).tiles
    assert source.state == "error"
    details = source_details(tiles({"health": health(problems=(scheduled,))}))
    assert '<li class="notice notice-error" data-problem="workgroup_missing"' in details
    assert "Reminders are still scheduled" in details
    # Nothing is missing: scheduled Reminders alone are no problem.
    assert problems(reminders_scheduled=True) == []


def test_one_backup_key_cause_gives_one_warning():
    """While the newest backup was sealed to another key than the configured
    one, the changed-key incident is not listed beside it."""
    incidents = {"backup_key_changed": NOW}
    assert problems(incidents=incidents) == ["backup_key_changed"]
    assert problems(incidents=incidents, backup_key="mismatch") == [
        "backup_key_mismatch"
    ]
    assert problems(backup_key="unchecked") == ["backup_key_unchecked"]


@pytest.mark.parametrize(
    ("configured", "fingerprint", "started", "key_set", "expected"),
    [
        # Sealed to another key, started after the configured key was set.
        ("aaaa", "bbbb", NOW, NOW - timedelta(hours=1), True),
        # The day between a key change and the next backup, or a backup
        # that started before the change and finished after it: no problem.
        ("aaaa", "bbbb", NOW - timedelta(hours=2), NOW - timedelta(hours=1), False),
        # A key set before any change event was recorded.
        ("aaaa", "bbbb", NOW, None, True),
        ("aaaa", "aaaa", NOW, None, False),
        # No configured key, or no backup yet.
        (None, "bbbb", NOW, None, False),
        ("aaaa", None, None, None, False),
    ],
)
def test_the_key_mismatch_rule(configured, fingerprint, started, key_set, expected):
    """``key_mismatch`` holds only for a backup started after the key was set
    (a backup reads its key as it starts)."""
    from parishkit.stewardship.system_health import key_mismatch

    facts = SimpleNamespace(
        backup_fingerprint=fingerprint,
        # Finished well after the key was set, whenever it started.
        backup_at=NOW + timedelta(hours=1),
        backup_started_at=started,
        key_set_at=key_set,
    )
    assert key_mismatch(configured, facts) is expected


def test_the_page_load_tiles_states_and_the_fragments_key():
    """Their states come from the page's reads, and travel as ?loaded=."""
    assert loaded_states(0, []) == {}
    assert loaded_states("no_data", "no_campaign") == {}
    assert loaded_states("no_campaign", "no_campaign") == {}
    assert loaded_states(3, [{"blocked": False}]) == {
        "source-form": "warning",
        "resave": "warning",
    }
    assert loaded_states(None, None) == {"source-form": "warning", "resave": "warning"}
    assert loaded_states(0, [{"blocked": False}, {"blocked": True}]) == {
        "resave": "error"
    }
    states = {"resave": "error", "source-form": "warning"}
    assert loaded_query(states) == "source-form:warning,resave:error"
    assert parse_loaded(loaded_query(states)) == states
    # Anything outside the closed set is ignored.
    assert parse_loaded("resave:working,mail:error,x,source-form:fine") == {}
    assert parse_loaded("") == {} and parse_loaded(None) == {}
    tiles = {tile.key: tile for tile in dashboard(health(), loaded=states).tiles}
    assert tiles["resave"].state == "error" and tiles["resave"].loaded
    assert tiles["source-form"].open


def test_nothing_waiting_and_everything_working_read_plainly():
    """A healthy system says so in the panel and the announcement."""
    healthy = health()
    assert not healthy.waiting
    assert dashboard(healthy).announcement == "System health: all working."
    paused = health(
        delivery_paused=True,
        problems=(Problem("schema_mismatch"), Problem("versions_differ")),
    )
    assert paused.waiting
    # Two problems in one tile are one Error tile (decision 2).
    assert dashboard(paused).announcement == (
        "System health: 1 error (Version and services)."
    )
    # The sentence follows the set of tiles, not only their number.
    swapped = health(
        problems=(Problem("sender_halted", (Subject("mail-dispatch", "mail"),)),)
    )
    assert dashboard(swapped).announcement == "System health: 1 error (Mail)."


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
        "workgroup_missing",
        "backup_key_mismatch",
        "backup_request_failed",
        "offsite_missing",
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
    region, body = page(
        {
            "health": shown,
            "campaign_id": None,
            "pause": {"actor": "admin@example.org", "reason": "Fixing a typo"},
        }
    )
    # Each problem in its one tile: the line counts the tiles.
    assert body.count("data-problem=") == len(codes)
    line = region[: region.index("</h2>")]
    assert "4 errors" in line and "and 1 warning" in line
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
    assert "data-live-since" in region and "Last checked" not in region
    # Internal names stay out of the words.
    assert "sender_halted<" not in body and "mail-dispatch<" not in body
    healthy = whole({"health": health(mode="testing")})
    assert "All working" in healthy
    assert "Nothing is holding Family email back." in healthy
    # Shown even when nothing waits, so the Administrator sees the limit coming.
    assert "Emails went to 0 recipients in the last 24 hours." in healthy
    assert "Debug logging is off in every service that reports." in healthy
    one = whole({"health": health(daily_sent=1)})
    assert "Emails went to 1 recipient in the last 24 hours." in one
    # Below the limit nothing waits for it, so no resume time is shown.
    assert "data-daily-resume" not in healthy and "data-daily-resume" not in one


def test_the_resume_time_shows_only_while_the_count_is_at_the_limit():
    """At the limit the count line says when sending resumes, as a local clock time."""
    resume = NOW + timedelta(hours=5, minutes=10)
    _, body = page({"health": health(daily_sent=1612, daily_resume_at=resume)})
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
    body = whole({"health": stopped})
    assert "Not running." in body
    assert (
        'href="https://github.com/epiphany40223/parishkit/blob/main/docs/guides/'
        'stewardship-deployment-runbook.md"' in body
    )


# ------------------------------------------------- the Mail tile (slice 3)


def senders(main="running", mail="running", **values):
    """The two mail consumers' lines in these sender states, beside the web
    portal; ``values`` go to both rows."""
    return group_processes(
        [
            row(),
            row("mail-dispatch", sender_state=main, **values),
            row("mail-dispatch", "mail", sender_state=mail, **values),
        ],
        NOW,
    )


def mail_said(model):
    """The Mail tile's counted summary row text, without its markup."""
    tile = section(whole({"health": model}), 'id="health-mail"')
    counted = tile.split('class="health-said">', 1)[1].split("</span>", 1)[0]
    return re.sub(r"<[^>]+>", "", counted).strip()


def test_the_mail_summary_names_the_first_wait_that_holds():
    """Each Working summary in MAIL_SUMMARIES's order: a pause outranks every
    limit, a limit outranks held planning and retries, and Sending or
    Nothing due only when nothing waits (decision 3: waits stay Working)."""
    until = NOW + timedelta(minutes=40)
    cases = [
        (
            health(
                delivery_paused=True,
                paused_at=NOW,
                processes=senders("daily_limit"),
                planning_held=True,
            ),
            "paused",
            f"Paused by an Administrator since {NOW.isoformat()}",
        ),
        (
            health(
                processes=senders("gmail_held", "daily_limit", sender_until=until),
                daily_resume_at=until,
                retry_waiting=2,
            ),
            "daily_limit",
            f"Waiting for the daily limit until about {until.isoformat()}",
        ),
        (
            health(processes=senders("running", "gmail_held", sender_until=until)),
            "gmail_held",
            f"Held at Gmail's limit until {until.isoformat()}",
        ),
        (
            health(planning_held=True, retry_waiting=3, send_active=True),
            "planning_held",
            "Planning is held",
        ),
        (
            health(retry_waiting=1, send_active=True),
            "retry",
            "1 email waiting to retry",
        ),
        (health(retry_waiting=3), "retry", "3 emails waiting to retry"),
        (health(send_active=True), "sending", "Sending"),
        (health(), "idle", "Nothing due"),
    ]
    assert {kind for _, kind, _ in cases} == set(MAIL_SUMMARIES)
    for model, kind, words in cases:
        assert model.mail_summary.kind == kind
        # Every wait is deliberate, so the tile stays Working and names it.
        assert dashboard(model).tiles[0].state == "working", kind
        assert mail_said(model) == words, kind


def test_mail_summary_times_are_browser_local_clock_times():
    """The summary's times read as a clock time (with "tomorrow" or the full
    date when another day), never with a zone label."""
    model = health(delivery_paused=True, paused_at=NOW)
    tile = section(whole({"health": model}), 'id="health-mail"')
    stamp = NOW.isoformat()
    assert (
        f'<time datetime="{stamp}" data-local-instant data-time-only data-time-day>'
        in tile
    )
    assert "UTC" not in tile


def test_gmails_limit_names_the_earliest_time_sending_resumes():
    """Both senders held at Gmail's limit until different times: the summary
    says the earlier, when sending resumes."""
    early, late = NOW + timedelta(hours=1), NOW + timedelta(hours=2)
    held = group_processes(
        [
            row("mail-dispatch", sender_state="gmail_held", sender_until=late),
            row("mail-dispatch", "mail", sender_state="gmail_held", sender_until=early),
        ],
        NOW,
    )
    summary = health(processes=held).mail_summary
    assert (summary.kind, summary.at) == ("gmail_held", early)


def test_a_limit_held_by_a_stopped_sender_is_not_named():
    """Only a running consumer's limit is a reason sends wait now."""
    stopped = group_processes(
        [row(), row("mail-dispatch", age=600, sender_state="gmail_held")], NOW
    )
    assert health(processes=stopped).mail_summary.kind == "idle"


def tiles(context):
    """The page's tiles alone, without the polled region's copies of them."""
    return page(context)[1]


def test_both_mail_senders_read_as_one_line_while_they_agree():
    """Two consumers in one state are one line; differing ones get a line
    each, and Why sends are waiting names a shared reason once."""
    agreed = tiles({"health": health()})
    assert agreed.count("data-sender=") == 1
    assert '<li data-sender="main-mail"><strong>Both mail senders:</strong>' in agreed
    since = NOW - timedelta(minutes=5)
    halted = tiles(
        {"health": health(processes=senders("halted", "halted", sender_since=since))}
    )
    assert "Both mail senders are halted." in halted
    assert halted.count("are halted.") == 1 and "is halted." not in halted
    differ = tiles({"health": health(processes=senders("halted", "running"))})
    assert differ.count("data-sender=") == 2
    assert "Mail sender 1 is halted." in differ
    assert '<li data-sender="mail"><strong>Mail sender 2:</strong>' in differ
    # Halts that began at different times are different lines.
    apart = health(
        processes=group_processes(
            [
                row("mail-dispatch", sender_state="halted", sender_since=since),
                row("mail-dispatch", "mail", sender_state="halted", sender_since=NOW),
            ],
            NOW,
        )
    )
    assert len(apart.sender_groups) == 2
    # A stopped consumer is always its own line, with its own last report,
    # even when both stopped reporting at once.
    stopped = health(
        processes=group_processes(
            [row("mail-dispatch", age=600), row("mail-dispatch", "mail", age=600)],
            NOW,
        )
    )
    assert len(stopped.sender_groups) == 2
    body = tiles({"health": stopped})
    assert body.count("Only the server operator can start it:") == 2


def tip_texts(body):
    """The text of every toggletip bubble in ``body``."""
    return re.findall(r'class="toggletip-bubble" id="[^"]+" hidden>([^<]*)<', body)


def test_mail_explanations_sit_in_toggletips_beside_their_reason():
    """Why each wait happens is one click away (ui-conventions "Page help");
    the reason, its time and its links stay visible."""
    until = NOW + timedelta(minutes=40)
    held = tiles(
        {
            "health": health(
                processes=senders("daily_limit", "outage_paused", sender_until=until),
                planning_held=True,
            )
        }
    )
    tips = " ".join(tip_texts(held))
    for words in (
        "Sending resumes by itself as the last 24 hours' count falls",
        "No new Family emails are being prepared right now",
        "After the email provider fails, sending pauses for a while",
    ):
        assert words in tips, words
        # The explanation is not also in the visible text.
        assert held.count(words) == 1, words
    assert "Waiting for the daily limit (Mail sender 1)." in held
    assert 'aria-controls="mail-wait-main-tip"' in held
    assert 'aria-describedby="mail-planning"' in held
    # The outage pause links its runbook, as the spec asks.
    assert "stewardship-launch-runbooks.md#mail-provider-outage" in held
    halted = tiles({"health": health(processes=senders("halted", "running"))})
    assert "A fault that waiting cannot fix stopped all sending" in " ".join(
        tip_texts(halted)
    )
    assert 'id="mail-sender-main-tip"' in halted


def test_an_unrecorded_follow_up_refusal_is_a_problem_linking_its_email():
    """#1201: listed after the mail incidents while any address is counted,
    saying what to do and linking the newest email's Mail message page."""
    message = str(uuid4())
    assert "refusal_unrecorded" not in problems(unrecorded=Unrecorded(0, None, None))
    found = find_problems(
        mode="testing",
        processes=(),
        missing=(),
        schema_current=False,
        incidents={"mail_provider_unavailable": NOW},
        refresh=None,
        backup_at=None,
        unrecorded=Unrecorded(1, NOW, message),
    )
    assert [problem.kind for problem in found] == [
        "mail_provider_unavailable",
        "refusal_unrecorded",
        "schema_mismatch",
    ]
    one = render_to_string(
        "stewardship/system-health-problem.html", {"problem": found[1]}
    )
    assert "An address the mail service refused permanently could not" in one
    assert "use Report a bounced address to set it aside" in one
    assert f'href="/admin/mail/sent/{message}/">See the email</a>' in one
    several = Problem("refusal_unrecorded", at=NOW, count=3, message_id=message)
    body = render_to_string(
        "stewardship/system-health-problem.html", {"problem": several}
    )
    assert "3 addresses the mail service refused permanently" in body
    assert "See the newest email</a>" in body
    assert several.summary == "refused address not set aside"


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
    _, body = page({"health": health(problems=found)})
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
    assert dashboard(health(problems=found)).announcement == (
        "System health: 2 errors (Mail, Version and services) and 1 warning "
        "(Debug logging)."
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
    body = whole({"health": health(problems=(debug,))})
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
        ("held", "is held while a Family email send is in progress"),
        ("not_picked_up", "The server did not pick up the backup requested at"),
        ("running", "and is running; it started at"),
        ("did_not_finish", "did not finish."),
        ("failed", "failed. Ask the server operator"),
        ("finished", "The backup requested at"),
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
        # A request that ended without a backup is the tile's problem.
        failed = code in {"not_picked_up", "did_not_finish", "failed"}
        listed = (Problem("backup_request_failed", at=NOW, detail=code),)
        body = whole(
            {
                "health": health(
                    backup_request=request, problems=listed if failed else ()
                )
            }
        )
        assert words in body, code
        assert f'data-backup-request="{code}"' in body
    body = whole({"health": health()})
    assert "Its size is 3.0" in body and "taken by version 1.4.2" in body


def backup_answer(**result):
    """Take a backup now's review dialog fragment for one step's result."""
    return render_to_string(
        "stewardship/system-health-backup-review.html",
        {"health": health(), "poll_interval": 10000, "backup_now": result},
    )


def test_take_a_backup_now_opens_its_confirmation_in_the_review_dialog():
    """The button posts the preview step to the page's review dialog (#1356),
    not in place, and the page draws that dialog, a confirmation redrawn from
    Apply's answer, with the region's status line after the region."""
    body = whole({"health": health()})
    region = section(body, 'id="backup-now"')
    assert 'data-review-open="backup-review"' in region
    assert 'value="preview"' in region and "data-in-place=" not in region
    assert 'action="/admin/system/health/"' in region
    assert 'id="backup-now-outcome"' in body
    assert body.index('id="backup-now-outcome"') > body.index('id="backup-now"')
    page = (TEMPLATES / "system-health.html").read_text()
    assert 'dialog_id="backup-review"' in page and "review=True" in page
    assert 'describedby="backup-review-summary"' in page
    assert 'outcome_id="backup-now-outcome"' in page and "redraw_body=True" in page
    assert 'translate "Take a backup now?" as backup_title' in page
    # The request's retention moved to the page's help, out of the preview.
    assert "follows the usual retention rules" in page


def test_the_backup_confirmation_says_only_what_the_page_does_not():
    """One sentence, off-site or not; the held-send warning only when a send
    is in progress; the key Apply posts. Never the last backup's time or
    size, which the Backups details show."""
    preview = BackupPreview(
        key="k-9", backup_at=NOW, backup_bytes=1024, offsite=True, held=False
    )
    text = backup_answer(preview=preview)
    assert '<div data-review-answer data-review-done="Backup requested.">' in text
    assert (
        '<p id="backup-review-summary">This asks the server to take one backup soon,'
        " of the same kind as the scheduled ones, and to copy it off-site to"
        " Google Drive.</p>"
    ) in text
    assert 'name="key" value="k-9" data-review-preview' in text
    assert "The last backup finished" not in text and "1.0" not in text
    assert "Family email send is in progress" not in text
    held = backup_answer(
        preview=BackupPreview(
            key="k-9", backup_at=None, backup_bytes=None, offsite=False, held=True
        )
    )
    assert "off-site copies are not set up, so it stays on the server only" in held
    assert "A Family email send is in progress, so the backup will wait" in held
    assert f'<a href="{reverse("admin:family_email_progress")}">' in held


def test_the_backup_request_and_refusals_answer_the_dialog():
    """Apply's answer draws the region again; each refusal is marked for the
    dialog to show as drawn, a session that ended meanwhile included."""
    done = backup_answer(requested=SimpleNamespace(created_at=NOW), created=True)
    assert 'id="backup-now"' in done and "data-review-answer" not in done
    for refused, words in (
        ("signed_out", "Your sign-in has ended"),
        ("busy", "A backup is already requested"),
        ("denied", "The server refused this backup request"),
        ("invalid", "That request could not be read."),
    ):
        text = backup_answer(refused=refused)
        assert text.strip().startswith("<div data-review-refusal>"), refused
        assert f'data-backup-refused="{refused}"' in text and words in text
        assert 'id="backup-now"' not in text
    ended = backup_answer(refused="signed_out")
    assert "Sign in again" in ended and "Nothing was requested." in ended
    assert "Confirm with Google" not in ended


def debug_page(
    *, switch=None, debug=True, actor="admin@example.org", mode="production", **values
):
    """Render the dashboard, whose Debug logging tile holds Turn off debug
    logging.

    ``switch`` is the newest debug-off switch; ``debug`` whether the web
    portal reports debug logging in effect; ``mode`` the global mode. ``values``
    are the view's other context (a step's ``debug_off`` result).
    """
    processes = group_processes([row(debug_logging=debug)], NOW)
    context = {
        "health": health(processes=processes, debug_switch=switch, mode=mode),
        "pause": None,
        "debug_actor": actor if switch is not None and switch.active else None,
        "poll_interval": 10000,
    } | values
    return whole(context)


def test_turn_off_debug_logging_is_offered_while_a_service_has_it_on():
    """The button posts the preview step to the page's review dialog
    (#1356); help says who clears it."""
    body = debug_page()
    assert 'value="debug_off_preview"' in body
    assert 'data-review-open="debug-off-review"' in body
    assert 'data-in-place="debug-off-preview"' not in body
    assert body.index('id="debug-off-outcome"') > body.index('id="debug-off"')
    page = (TEMPLATES / "system-health.html").read_text()
    assert 'dialog_id="debug-off-review"' in page
    assert 'apply_action="debug_off_confirm"' in page
    assert 'describedby="debug-off-review-summary"' in page
    assert 'translate "Turn off debug logging?" as debug_off_title' in page
    # What the switch leaves alone moved to the page's help.
    assert "before it first reads the switch" in page
    # The polled copy carries the same enabled button for the mirror.
    assert '<template data-live-mirror="debug-off-button">' in body
    # The intro's toggletip says what the switch does, not how a set switch
    # is cleared, and names no Allow button (not drawn yet).
    assert "each service reads within about a minute" in body
    intro = section(body, 'id="debug-off"')
    assert "Allow debug logging again" not in intro
    assert "those whose server setting is still on" not in intro
    testing = section(debug_page(mode="testing"), 'id="debug-off"')
    assert "Allow debug logging again" not in testing
    # In Production the debug_logging problem says what to do; the panel
    # does not say it again.
    assert "Debug logging must be off in Production" not in body


def test_a_set_switch_shows_only_who_and_when():
    """Who set it and when (browser-local), and that the setting still says
    on; no Turn off button, greyed or not (Administrator, 2026-10-15)."""
    switch = DebugSwitchStatus(set_at=NOW, cleared_at=None, cleared_by=None)
    body = debug_page(switch=switch)
    assert 'value="debug_off_preview"' not in body
    assert "debug-off-reason" not in body and "debug-allow-reason" not in body
    assert "Turn debug logging off in every service now" not in body
    assert 'data-debug-status="set"' in body
    assert "Debug logging was turned off by admin@example.org at" in body
    assert f'<time datetime="{NOW.isoformat()}" data-local-instant>' in body
    assert 'data-debug-switch="set"' in body
    assert "The setting on the server still says “on”." in body
    # Once no service reports debug logging, the line still says who and when.
    quiet = debug_page(switch=switch, debug=False)
    assert "Debug logging was turned off by admin@example.org at" in quiet
    assert 'data-debug-switch="set"' in quiet
    # No switch and no service in debug form: the plain status line only.
    unset = debug_page(debug=False)
    assert 'value="debug_off_preview"' not in unset
    assert "debug-off-reason" not in unset
    assert 'data-debug-status="off"' in unset
    assert "Debug logging is off in every service that reports." in unset
    assert "Turn debug logging off in every service now" not in unset
    # The controls' target waits while the pointer is over the grid, since
    # its states differ in height.
    target = '<div data-live-mirror-target="debug-off-button" data-live-mirror-hold>'
    assert target in body and target in unset


def test_a_cleared_switch_is_named_only_while_debug_logging_is_back():
    """The operator's clear is stated while it matters, not forever."""
    cleared = DebugSwitchStatus(
        set_at=NOW - timedelta(hours=2), cleared_at=NOW, cleared_by="operator"
    )
    body = debug_page(switch=cleared)
    assert "The server operator cleared the debug-off switch at" in body
    assert 'value="debug_off_preview"' in body
    assert "cleared the debug-off switch" not in debug_page(switch=cleared, debug=False)


def debug_off_answer(**result):
    """Turn off debug logging's review dialog fragment for one step's result."""
    return render_to_string(
        "stewardship/system-health-debug-off-review.html",
        {
            "health": health(
                processes=group_processes([row(debug_logging=True)], NOW),
                debug_switch=DebugSwitchStatus(
                    set_at=NOW, cleared_at=None, cleared_by=None
                ),
            ),
            "debug_actor": "admin@example.org",
            "poll_interval": 10000,
            "debug_off": result,
        },
    )


def test_the_debug_off_confirmation_and_each_refusal_answer_the_dialog():
    """The preview is one sentence naming the services with debug logging on,
    never the fixed bullets the About panel holds; Apply's answer draws the
    region again, with its status line; each refusal is marked for the dialog."""
    preview = DebugOffPreview(key="k-1", processes=("the web portal",))
    text = debug_off_answer(preview=preview)
    assert (
        '<div data-review-answer data-review-done="Debug logging turned off.">'
    ) in text
    assert (
        '<p id="debug-off-review-summary">This turns debug logging off now in the'
        " services that have it on (the web portal), without restarting them,"
        " until the debug-off switch is cleared.</p>"
    ) in text
    assert 'name="key" value="k-1" data-review-preview' in text
    for words in ("Lines already written", "still says", "Confirm: turn off"):
        assert words not in text, words
    quiet = debug_off_answer(preview=DebugOffPreview(key="k-1", processes=()))
    assert "No service reports debug logging right now" in quiet
    done = debug_off_answer(requested=SimpleNamespace(set_at=NOW), created=True)
    assert 'id="debug-off"' in done and "data-review-answer" not in done
    assert 'data-debug-status="set"' in done
    for refused, words in (
        ("signed_out", "Your sign-in has ended"),
        ("busy", "Debug logging has already been turned off"),
        ("denied", "The server refused to turn off debug logging"),
        ("invalid", "That request could not be read."),
    ):
        answer = debug_off_answer(refused=refused)
        assert answer.strip().startswith("<div data-review-refusal>"), refused
        assert f'data-debug-off-refused="{refused}"' in answer
        assert words in answer, refused


# ------------------------------------------------- accept this change once

FAILED = (
    DropCount("family", 1084, 612, 25, True),
    DropCount("member", 3000, 2900, 25, False),
    DropCount("email_eligible_families", 900, 500, 25, True),
)
ACCEPTANCE = UUID("00000000-0000-4000-8000-0000000000a5")
# The refused attempt the panel shows.
REFUSED_ATTEMPT = UUID("00000000-0000-4000-8000-0000000000a6")
# The problem that refusal opened, and an unrelated one.
DROP_PROBLEM = Problem("source_destructive_change", at=NOW)
LATE_PROBLEM = Problem("source_late", at=NOW)


def accepted(*, live=True):
    """An acceptance of the refusal shown, given at NOW."""
    return DropAcceptanceStatus(
        id=ACCEPTANCE, created_at=NOW, expires_at=NOW + timedelta(hours=24), live=live
    )


def accept_page(
    counts=FAILED,
    acceptance=None,
    actor="admin@example.org",
    request=None,
    problems=(),
    **values,
):
    """Render the dashboard, whose ParishSoft refresh tile holds the Accept
    this change once region under the refusal.

    ``values`` are the view's other context (a step's ``accept`` result, the
    page's ``status_url``).
    """
    context = {
        "health": health(
            refused_at=NOW,
            refused_counts=counts,
            drop_acceptance=acceptance,
            problems=problems,
        ),
        "pause": None,
        "accept_actor": actor if acceptance is not None and acceptance.live else None,
        "refused_attempt": REFUSED_ATTEMPT if counts else None,
        "poll_interval": 10000,
    } | values
    return whole(context, request=request)


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


def test_the_preview_document_holds_counts_example_duids_and_the_binding_only():
    """``system refresh-accept-preview`` prints counts and example Family
    DUIDs (PR 5c), never names."""
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
        examples={"family": [101, 102]},
        preview={"token": "signed"},
    ).to_document()
    assert set(document) == {
        "attempt_id",
        "refused_at",
        "counts",
        "examples",
        "preview",
    }
    assert document["examples"] == {"family": [101, 102]}
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
    assert '<template data-live-mirror="accept-offer">' in body
    assert 'data-live-mirror-target="accept-offer"' in body
    assert 'aria-controls="accept-change-intro-tip"' in body
    assert "accepted once with Accept this change once just below" in body
    assert "reload System health" not in body
    # The emergency path stays the operator's, named in the help.
    assert "the server operator can still let a change through" in body


def test_the_accept_region_sits_right_under_the_refusal():
    """In the ParishSoft refresh tile's details, directly after its problems,
    which end with the refused change's counts (#1214), outside the polled
    region; its heading is a subheading of the tile's. The polled copy of
    its section carries no CSRF token, so polls compare equal; the page's
    own copy does. With no refusal only the empty region and its mirror
    target are drawn, for a poll to fill (batch 24a)."""
    import re

    from django.test import RequestFactory

    request = RequestFactory().get("/admin/system/health/")
    region, tiles = page(
        {
            "health": health(refused_at=NOW, refused_counts=FAILED),
            "refused_attempt": REFUSED_ATTEMPT,
            "status_url": "/admin/system/health/status/",
        },
        request=request,
    )
    details = tiles[tiles.index('id="health-source-details"') :]
    before, after = details.split('<div data-live-keyed="accept-change">', 1)
    # The refusal's counts end the problems, then at once the region.
    assert before.rstrip().endswith("</tbody></table></div>\n</div>\n</div>\n</div>")
    assert "data-refused-change" in before
    assert after.startswith(
        '<div id="accept-change" data-in-place-region>'
        '<div data-live-mirror-target="accept-offer" data-live-mirror-hold>'
        '<section class="panel" aria-labelledby="accept-change-heading">'
    )
    assert '<h3 id="accept-change-heading">Accept this change once</h3>' in after
    assert tiles.count('id="accept-change"') == 1
    assert 'id="accept-change"' not in region
    token = r'name="csrfmiddlewaretoken" value="([^"]*)"'
    assert re.search(token, section(tiles, 'id="accept-change"')).group(1)
    polled = region[region.index('<template data-live-mirror="accept-offer">') :]
    assert re.search(token, polled).group(1) == ""
    # The region names the refusal shown, for an open preview's check.
    assert f'data-live-key-value="{REFUSED_ATTEMPT}"' in region
    # With no refusal nothing is drawn: the region and its target are empty,
    # and so is the polled copy, until a poll shows a refusal.
    empty = accept_page(counts=())
    assert (
        '<div id="accept-change" data-in-place-region>'
        '<div data-live-mirror-target="accept-offer" data-live-mirror-hold></div>'
    ) in empty
    assert '<template data-live-mirror="accept-offer"></template>' in empty
    shown = empty[: empty.index('<template data-live-mirror="accept-button">')]
    shown += empty[empty.index('<template data-live-mirror="backup-now-button">') :]
    assert "accept-change-heading" not in shown
    assert "data-accept-state" not in shown
    assert 'data-live-key="accept-change" data-live-key-value=""' in empty
    assert "data-refused-change" not in empty


def test_no_accept_section_while_parishsoft_is_healthy():
    """A healthy ParishSoft refresh has nothing to accept, so neither the
    page nor the polled copy draws Accept this change once (batch 24a)."""
    region, tiles = page({"health": health()})
    assert '<template data-live-mirror="accept-offer"></template>' in region
    assert "Accept this change once" not in tiles
    assert "accept-change-heading" not in tiles
    assert "data-accept-state" not in tiles
    assert 'value="accept_preview"' not in tiles


def test_an_accepted_refusal_is_listed_but_not_counted():
    """Accepted once, the refusal's problem says by whom and that the next
    full refresh is awaited, and no longer counts as needing attention;
    every other problem still does (#530)."""
    both = (LATE_PROBLEM, DROP_PROBLEM)
    open_ = health(refused_counts=FAILED, problems=both)
    assert open_.attention == both
    done = health(refused_counts=FAILED, problems=both, drop_acceptance=accepted())
    assert done.attention == (LATE_PROBLEM,)
    assert dashboard(done).announcement == (
        "System health: 1 warning (ParishSoft refresh)."
    )
    alone = health(
        refused_counts=FAILED, problems=(DROP_PROBLEM,), drop_acceptance=accepted()
    )
    assert alone.attention == ()
    assert dashboard(alone).announcement == "System health: all working."
    # A lapsed acceptance is not an answer: the refusal counts again.
    lapsed = health(
        refused_counts=FAILED,
        problems=(DROP_PROBLEM,),
        drop_acceptance=accepted(live=False),
    )
    assert lapsed.attention == (DROP_PROBLEM,)

    def source(body):
        """The ParishSoft refresh tile's summary row."""
        return section(body, 'id="health-source"')

    body = accept_page(acceptance=accepted(), problems=both)
    assert "health-counted status-warning" in source(body)
    line = body[body.index('data-problem="source_destructive_change"') :]
    line = line[: line.index("</li>")]
    assert "data-problem-handled" in line and "notice-error" not in line
    assert (
        "The refused ParishSoft change was accepted once by admin@example.org; "
        "waiting for the next full refresh." in line
    )
    assert "refused because some counts fell too far" not in line
    body = accept_page(acceptance=accepted(), problems=(DROP_PROBLEM,))
    assert "health-counted status-working" in source(body)
    assert 'data-problem="source_destructive_change"' in body
    # Not yet accepted: the refusal's own sentence, counted as an error.
    body = accept_page(problems=(DROP_PROBLEM,))
    assert "health-counted status-error" in source(body)
    assert "refused because some counts fell too far" in body


def test_the_preview_and_the_panel_name_the_refusal():
    """The refusal notice and the kept element name the refusal shown; the
    preview names the one it reviews, with a reserved line and a Confirm
    that live-status-v1.js holds once they differ."""
    preview = AcceptPreview(
        token="signed", attempt_id=uuid4(), refused_at=NOW, counts=FAILED
    )
    body = accept_page(accept={"preview": preview})
    assert f'data-refused-attempt="{REFUSED_ATTEMPT}"' in body
    assert f'data-live-key-value="{REFUSED_ATTEMPT}"' in body
    region = section(body, 'id="accept-change"')
    assert f'data-live-keep-for="{preview.attempt_id}"' in region
    assert (
        '<p class="reserve-lines-wrap" role="status" data-live-stale-note='
        '"This changed since you looked; review it again."></p>' in region
    )
    confirm = region[: region.index("Confirm: accept this change once")]
    assert "data-live-stale-holds" in confirm[confirm.rindex("<button") :]
    # No refusal shown: the key is empty, so any open preview is stale.
    assert 'data-live-key-value=""' in accept_page(counts=())


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


def unacceptable_page(*, acceptance=None, counts=FAILED, problems=(DROP_PROBLEM,)):
    """``(region, tiles)`` for a refusal that cannot be accepted, rendered
    as the page draws it, with the refusal's problem open by default."""
    return page(
        {
            "health": health(
                refused_at=NOW,
                refused_counts=counts,
                drop_acceptance=acceptance,
                problems=problems,
            ),
            "refused_attempt": REFUSED_ATTEMPT,
        }
    )


def assert_no_accept_section(region, tiles):
    """Neither the page's tile nor the polled copy draws Accept this change
    once: the region and its mirror target stay empty (2026-10-15)."""
    assert (
        '<div id="accept-change" data-in-place-region>'
        '<div data-live-mirror-target="accept-offer" data-live-mirror-hold></div>'
    ) in tiles
    assert '<template data-live-mirror="accept-offer"></template>' in region
    assert "accept-change-heading" not in tiles
    assert "data-accept-state" not in tiles
    assert 'value="accept_preview"' not in tiles
    assert "accepted once with Accept this change once" not in tiles


def test_a_lapsed_acceptance_draws_no_section_and_says_why():
    """A refusal is accepted once: once its acceptance lapsed unused there is
    nothing to accept, so no section is drawn, and the problem line says
    when it was accepted, why it cannot be again, and links Refresh now's
    page for a new refusal (Administrator, 2026-10-15)."""
    region, tiles = unacceptable_page(acceptance=accepted(live=False))
    assert_no_accept_section(region, tiles)
    line = tiles[tiles.index('data-problem="source_destructive_change"') :]
    line = line[: line.index("data-refused-change")]
    assert 'data-accept-unavailable="lapsed"' in line
    assert "It was accepted once at" in line
    assert f'<time datetime="{NOW.isoformat()}" data-local-instant>' in line
    assert "the acceptance ended before a full refresh used it" in line
    assert (
        "refused too until the drop is fixed in ParishSoft; Refresh now gets a "
        "new refusal to review and accept." in line
    )
    assert 'href="/admin/parish/parishsoft-refresh/"' in line
    # The polled copy of the problems says the same, so a poll keeps it.
    assert 'data-accept-unavailable="lapsed"' in region
    # With the problem no longer open, the counts' own notice says why.
    region, tiles = unacceptable_page(acceptance=accepted(live=False), problems=())
    assert_no_accept_section(region, tiles)
    assert 'data-accept-unavailable="lapsed"' in tiles
    assert "Accept this change once just below" not in tiles


def test_an_empty_load_draws_no_section_and_says_why():
    """Refused at any setting, by design: no section, and the problem line
    says it can never be accepted (Administrator, 2026-10-15)."""
    for counts in (
        (DropCount("family", 1084, 0, 25, True),),
        (DropCount("member", 3000, 0, 25, True),),
    ):
        region, tiles = unacceptable_page(counts=counts)
        assert_no_accept_section(region, tiles)
        line = tiles[tiles.index('data-problem="source_destructive_change"') :]
        line = line[: line.index("data-refused-change")]
        assert 'data-accept-unavailable="empty"' in line
        assert "can never be accepted" in line
        assert 'href="/admin/parish/parishsoft-refresh/"' in line


def test_home_keeps_the_refusal_sentence_for_an_unacceptable_refusal():
    """Home has no ``health``, so it keeps the refusal's own sentence; the
    reason is the System health page's (slice 7 reworks Home)."""
    body = render_to_string(
        "stewardship/system-health-problem.html", {"problem": DROP_PROBLEM}
    )
    assert "data-accept-unavailable" not in body
    assert "refused because some counts fell too far" in body


def test_a_step_result_stays_when_the_refusal_cannot_be_accepted():
    """A step's result is the Administrator's own answer, so it stays; in
    place of the button one line points at the problem line's reason."""
    body = accept_page(
        acceptance=accepted(live=False),
        problems=(DROP_PROBLEM,),
        accept={"refused": "busy"},
    )
    region = section(body, 'id="accept-change"')
    assert 'data-accept-refused="changed"' in region
    assert 'data-accept-state="lapsed"' in region
    assert "This refusal cannot be accepted; the refused change above says why." in (
        region
    )
    assert "<button" not in region
    # Its height changes with the state, so it waits while the pointer is
    # over the grid, as the problems do.
    assert (
        '<div data-live-mirror-target="accept-button" data-live-mirror-hold>' in region
    )


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
    assert "data-live-mirror-target" not in section(body, 'id="accept-change"')


def test_the_preview_lists_example_families_under_each_failing_count():
    """PR 5c: name and Family DUID under the failing count; a failing
    Family, Member or contact count without examples says none could be
    recorded; a passing count, or a ministry count, lists nothing."""
    counts = (*FAILED, DropCount("ministry", 40, 20, 25, True))
    preview = AcceptPreview(
        token="signed-binding",
        attempt_id=uuid4(),
        refused_at=NOW,
        counts=counts,
        examples={"family": ((1201, "Example, Ann & Bob"), (1202, "Sample, Carol"))},
    )
    body = accept_page(accept={"preview": preview})
    region = body[body.index('id="accept-change"') :]
    family = region[region.index('data-accept-examples="family"') :]
    family = family[: family.index("</table>")]
    assert "Examples to check in ParishSoft:" in family
    # The name is escaped, and the DUID has its own column.
    assert '<td>Example, Ann &amp; Bob</td><td class="numeric">1201</td>' in family
    assert '<td>Sample, Carol</td><td class="numeric">1202</td>' in family
    email = region[region.index('data-accept-examples="email_eligible_families"') :]
    assert "No example Families are available for this count." in email
    assert 'data-accept-examples="member"' not in region
    assert 'data-accept-examples="ministry"' not in region
    assert 'aria-controls="accept-change-examples-tip"' in region
    assert "deleted when the change is accepted" in region


def test_the_examples_help_shows_only_when_a_failing_count_gets_examples():
    """A refusal whose only failing count is a ministry count lists no
    examples, so the page does not explain them."""
    preview = AcceptPreview(
        token="signed-binding",
        attempt_id=uuid4(),
        refused_at=NOW,
        counts=(
            DropCount("family", 1084, 1000, 25, False),
            DropCount("ministry", 40, 20, 25, True),
        ),
    )
    body = accept_page(accept={"preview": preview})
    region = body[body.index('id="accept-change"') :]
    assert "data-accept-examples" not in region
    assert "accept-change-examples-tip" not in region
    assert "example Families show where to look" not in region


@pytest.mark.parametrize(
    ("refused", "marker", "words"),
    [
        ("signed_out", "signed_out", "Your sign-in has ended"),
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


SET = DebugSwitchStatus(set_at=NOW, cleared_at=None, cleared_by=None)


def test_allow_debug_logging_again_is_offered_only_in_testing_while_set():
    """Testing with the switch set: the button posts the preview to its
    review dialog (#1356), and the polled copy carries it. Otherwise it is
    not drawn at all (Administrator, 2026-10-15): never in Production, nor
    without a switch."""
    body = debug_page(switch=SET, debug=False, mode="testing")
    assert 'value="debug_allow_preview"' in body
    assert 'data-review-open="debug-allow-review"' in body
    assert "data-in-place=" not in section(body, 'id="debug-off"')
    page = (TEMPLATES / "system-health.html").read_text()
    assert 'dialog_id="debug-allow-review"' in page
    assert 'apply_action="debug_allow_confirm"' in page
    assert 'describedby="debug-allow-review-summary"' in page
    assert 'translate "Allow debug logging again?" as debug_allow_title' in page
    assert body.count('value="debug_allow_preview"') == 2  # region and mirror
    assert "In Testing, Allow debug logging again on this page clears" in body
    production = debug_page(switch=SET, debug=False)
    assert 'value="debug_allow_preview"' not in production
    assert ">Allow debug logging again<" not in production
    assert "debug-allow-reason" not in production
    # The status line's toggletip says only the server operator clears it.
    assert "using pk-stewardship debug-off-clear; this page cannot." in production
    unset = debug_page(mode="testing")
    assert 'value="debug_allow_preview"' not in unset
    assert ">Allow debug logging again<" not in unset
    assert "The debug-off switch is not set." not in unset


def debug_allow_answer(**result):
    """Allow debug logging again's review dialog fragment for one result."""
    cleared = DebugSwitchStatus(
        set_at=NOW - timedelta(hours=1), cleared_at=NOW, cleared_by="administrator"
    )
    return render_to_string(
        "stewardship/system-health-debug-allow-review.html",
        {
            "health": health(debug_switch=cleared, mode="testing"),
            "debug_actor": None,
            "poll_interval": 10000,
            "debug_off": {"allow": True} | result,
        },
    )


def test_the_allow_confirmation_result_and_refusals_answer_the_dialog():
    """Allow debug logging again's preview is one sentence that keeps the
    personal-information caution, never the switch's set time the page
    shows; Apply's answer draws Turn off debug logging's region again; each
    refusal is marked for the dialog, in its own words."""
    preview = DebugAllowPreview(key="k-2", set_at=NOW)
    text = debug_allow_answer(preview=preview)
    assert (
        '<div data-review-answer data-review-done="Debug logging allowed again.">'
    ) in text
    assert (
        '<p id="debug-allow-review-summary">This clears the debug-off switch:'
        " within about a minute, services started with debug logging on log in"
        " debug form again, which can record Families' personal"
        " information.</p>"
    ) in text
    assert 'name="key" value="k-2" data-review-preview' in text
    assert "set at" not in text and NOW.isoformat() not in text
    assert "Confirm: allow debug logging again" not in text
    done = debug_allow_answer(allowed=SimpleNamespace(cleared_at=NOW), created=True)
    assert 'id="debug-off"' in done and "data-review-answer" not in done
    for refused, words in (
        ("signed_out", "Your sign-in has ended"),
        ("busy", "The debug-off switch is no longer set"),
        ("production", "In Production only the server operator can clear"),
        ("denied", "The server refused to allow debug logging again"),
        ("invalid", "That request could not be read."),
    ):
        answer = debug_allow_answer(refused=refused)
        assert answer.strip().startswith("<div data-review-refusal>"), refused
        assert f'data-debug-allow-refused="{refused}"' in answer
        assert words in answer, refused


# ------------------------------------- the ParishSoft refresh tile (slice 4)


def source_said(model, **context):
    """The ParishSoft refresh tile's counted summary row text, without markup."""
    tile = section(whole({"health": model} | context), 'id="health-source"')
    counted = tile.split('class="health-said">', 1)[1].split("</span>", 1)[0]
    return re.sub(r"<[^>]+>", "", counted).strip()


def test_the_source_summary_names_the_first_that_holds():
    """Each Working summary in SOURCE_SUMMARIES's order: an accepted refusal
    outranks a hold, a hold catching up after its send outranks one still
    held, a hold a running refresh, and the data's age with the
    connection comes only when nothing else holds."""
    before = NOW - timedelta(hours=2)
    until = NOW + timedelta(hours=1)
    working = Connection("working", NOW)

    def status(**values):
        """A refresh status with data as of ``before`` and these values."""
        running = values.pop("running", False)
        return FullRefreshStatus(
            before, None, running, data_as_of=before, connection=working, **values
        )

    cases = [
        (
            health(
                refresh=status(held_for_send=True, resume_at=until, running=True),
                refused_counts=FAILED,
                problems=(DROP_PROBLEM,),
                drop_acceptance=accepted(),
            ),
            "accepted",
            "Change accepted once; waiting for the next full refresh",
        ),
        (
            health(
                refresh=status(
                    held_for_send=True,
                    out_of_date=True,
                    overdue_at=before,
                    resume_at=until,
                    running=True,
                )
            ),
            "held",
            f"Refreshes held during the email send until about {until.isoformat()}",
        ),
        (
            health(
                refresh=status(
                    held_for_send=True,
                    catching_up=True,
                    out_of_date=True,
                    overdue_at=before,
                    # The resume time has passed, so it is never named.
                    resume_at=NOW - timedelta(minutes=5),
                    running=True,
                )
            ),
            "catching_up",
            "Catching up after the email send",
        ),
        (health(refresh=status(running=True)), "running", "Full refresh running"),
        (
            health(refresh=status()),
            "data",
            f"Data as of {before.isoformat()}; connection working",
        ),
        (
            health(refresh=FullRefreshStatus(None, None, False)),
            "none",
            "No ParishSoft data yet",
        ),
    ]
    assert {kind for _, kind, _ in cases} == set(SOURCE_SUMMARIES)
    for model, kind, words in cases:
        assert model.source_summary.kind == kind
        assert dashboard(model).tiles[1].state == "working", kind
        assert source_said(model) == words, kind
    assert health(refresh=None).source_summary.kind == "none"
    # A lapsed acceptance is no answer: the refusal counts again, so the
    # summary is the refusal's own words.
    lapsed = health(
        refused_counts=FAILED,
        problems=(DROP_PROBLEM,),
        drop_acceptance=accepted(live=False),
    )
    assert lapsed.source_summary.kind != "accepted"
    assert dashboard(lapsed).tiles[1].state == "error"


def test_the_source_summary_states_the_connection_line():
    """The data's age carries the connection line's state in words, or
    nothing when the status has none."""
    since = NOW - timedelta(hours=5)

    def said(connection):
        """The summary for data as of NOW with this connection."""
        refresh = FullRefreshStatus(
            NOW, None, False, data_as_of=NOW, connection=connection
        )
        return source_said(health(refresh=refresh))

    stamp = NOW.isoformat()
    assert said(Connection("not_checked", since)) == (
        f"Data as of {stamp}; connection not checked since {since.isoformat()}"
    )
    assert said(Connection("unknown")) == (
        f"Data as of {stamp}; connection not checked yet"
    )
    assert said(Connection("failing", since)) == (
        f"Data as of {stamp}; connection failing since {since.isoformat()}"
    )
    assert said(None) == f"Data as of {stamp}"


def test_source_summary_times_are_browser_local_clock_times():
    """ "Data as of 6:02 AM": a clock time today, the full date otherwise,
    never a zone label."""
    refresh = FullRefreshStatus(
        NOW, None, False, data_as_of=NOW, connection=Connection("working", NOW)
    )
    tile = section(whole({"health": health(refresh=refresh)}), 'id="health-source"')
    assert (
        f'<time datetime="{NOW.isoformat()}" data-local-instant data-time-only '
        "data-time-day>" in tile
    )
    assert "UTC" not in tile


def source_details(body):
    """The ParishSoft refresh tile's details band, up to the next tile."""
    start = body.index('id="health-source-details"')
    return body[start : body.index('id="health-backups"', start)]


def test_the_refusal_holds_its_counts_and_ends_the_problems():
    """The refused change's item holds the counts table and comes after the
    other problems and the unknown WorkGroup, so Accept this change once
    follows the counts directly; its explanations sit in a toggletip and
    the page drops the item's self-link."""
    listed = (
        DROP_PROBLEM,
        LATE_PROBLEM,
        Problem("source_tenant_mismatch", at=NOW),
        Problem("source_retention_failing", at=NOW),
    )
    listed += (Problem("workgroup_missing", workgroup="No reminders"),)
    model = health(refused_at=NOW, refused_counts=FAILED, problems=listed)
    (_, source, *_) = dashboard(model).tiles
    assert [part.problem.kind for part in source.listed] == [
        "source_late",
        "source_tenant_mismatch",
        "source_retention_failing",
        "workgroup_missing",
    ]
    assert source.refusal.problem == DROP_PROBLEM
    details = source_details(
        tiles({"health": model, "refused_attempt": REFUSED_ATTEMPT})
    )
    order = re.findall(r'data-problem="([a-z_]+)"', details)
    assert order == [
        "source_late",
        "source_tenant_mismatch",
        "source_retention_failing",
        "workgroup_missing",
        "source_destructive_change",
    ]
    item = details[details.index('data-problem="source_destructive_change"') :]
    item, after = item.split("</li>", 1)
    assert "data-refused-change" in item and "Families with an email address" in item
    assert after.lstrip().startswith(
        '</ul>\n\n</div>\n<div data-live-keyed="accept-change">'
    )
    # The WorkGroup item matches its tile's Warning, with its setting's link.
    workgroup = details[details.index('data-problem="workgroup_missing"') :]
    workgroup = workgroup[: workgroup.index("</li>")]
    assert '<li class="notice" data-problem="workgroup_missing"' in details
    assert "Check the No-reminders WorkGroup setting" in workgroup
    # A missing name keeps the set applied before (#1305).
    assert (
        "Until a refresh finds it, only the Families skipped before stay skipped."
    ) in workgroup
    # Said once: the sentence states the refusal and what to do.
    assert details.count("refused because some counts fell too far") == 1
    assert "Check these numbers" not in details
    assert "See the counts" not in details
    tip = " ".join(tip_texts(details))
    assert "highest value over the full refreshes of the past week" in tip
    assert "the system keeps using the data it had" in tip
    assert details.count("highest value over") == 1
    assert 'aria-describedby="refused-counts-caption"' in details


def test_home_keeps_the_refusals_link_to_its_counts():
    """Home's sentence, drawn without the page's model, still links the tile."""
    line = render_to_string(
        "stewardship/system-health-problem.html", {"problem": DROP_PROBLEM}
    )
    assert 'href="/admin/system/health/#health-source">See the counts</a>' in line


def test_the_source_panel_repeats_no_problem_sentence():
    """A refused organization, old copies not removed, a failed and a late
    refresh are each said once, by the tile's problems; the panel links the
    runbook those sentences name and keeps the failure's details link."""
    task = uuid4()
    refresh = FullRefreshStatus(
        NOW - timedelta(days=1),
        NOW - timedelta(hours=1),
        False,
        failed_task_id=task,
        overdue_at=NOW - timedelta(hours=2),
        out_of_date=True,
        late_minutes=90,
    )
    model = health(
        refresh=refresh,
        incidents={
            "source_tenant_mismatch": NOW,
            "source_retention_failing": NOW,
        },
        problems=find_problems(
            mode="production",
            processes=(),
            missing=(),
            schema_current=True,
            incidents={
                "source_tenant_mismatch": NOW,
                "source_retention_failing": NOW,
            },
            refresh=refresh,
            backup_at=NOW,
        ),
    )
    details = source_details(tiles({"health": model}))
    for words in (
        "reaches a different organization than before",
        "Old ParishSoft copies are not being removed",
        "did not finish",
        "is late",
    ):
        assert details.count(words) == 1, words
    assert "minutes late" not in details
    assert "stewardship-launch-runbooks.md#parishsoft-outage" in details
    assert f"/admin/system/background/{task}/" in details
    assert "appear after the next one" in details
    # Elsewhere the shared refresh status keeps its notices.
    shared = render_to_string(
        "stewardship/full-refresh-status.html",
        {"status": refresh, "can_view_task": True},
    )
    assert "did not finish" in shared and "minutes late" in shared
    # Without either incident, no runbook line.
    assert "parishsoft-outage" not in source_details(tiles({"health": health()}))


def test_a_failed_refresh_stays_in_the_tile_while_parishsoft_is_not_answering():
    """With the connection failing, ``source_failing`` is listed instead of
    ``source_failed``, so the panel keeps the failed refresh's own notice;
    once only the failure is listed, the panel shortens it."""
    task = uuid4()
    failed_at = NOW.replace(hour=2)

    def details(connection):
        """The ParishSoft refresh tile's details for a 2 AM failure."""
        refresh = FullRefreshStatus(
            NOW - timedelta(days=1),
            failed_at,
            False,
            failed_task_id=task,
            connection=connection,
        )
        found = find_problems(
            mode="production",
            processes=(),
            missing=(),
            schema_current=True,
            incidents={},
            refresh=refresh,
            backup_at=NOW,
        )
        model = health(refresh=refresh, problems=found)
        return model, source_details(tiles({"health": model}))

    model, failing = details(Connection("failing", failed_at))
    assert [problem.kind for problem in model.problems] == ["source_failing"]
    assert not model.failed_listed
    assert "ParishSoft has not answered since" in failing
    assert failing.count("did not finish") == 1
    assert 'class="notice notice-error" role="status"' in failing
    assert f"/admin/system/background/{task}/" in failing
    model, failed = details(Connection("working", NOW))
    assert model.failed_listed
    assert failed.count("did not finish") == 1
    assert 'class="notice notice-error" role="status"' not in failed
    assert f"/admin/system/background/{task}/" in failed


def test_an_organization_mismatch_is_never_said_as_could_not_be_reached():
    """A refresh refused because the key sees another organization reached
    ParishSoft: System health names the mismatch, not "ParishSoft could not
    be reached", though its refreshes fail (#1492). Without the mismatch, a
    failing connection is still "could not be reached"."""
    failed_at = NOW.replace(hour=2)
    refresh = FullRefreshStatus(
        NOW - timedelta(days=1),
        failed_at,
        False,
        failed_task_id=uuid4(),
        connection=Connection("failing", failed_at),
    )

    def kinds(incidents):
        """The listed problem kinds for these open incidents."""
        found = find_problems(
            mode="production",
            processes=(),
            missing=(),
            schema_current=True,
            incidents=incidents,
            refresh=refresh,
            backup_at=NOW,
        )
        return [problem.kind for problem in found]

    assert kinds({"source_tenant_mismatch": NOW}) == [
        "source_failed",
        "source_tenant_mismatch",
    ]
    words = [
        Problem(kind, at=NOW).summary for kind in kinds({"source_tenant_mismatch": NOW})
    ]
    assert "ParishSoft organization does not match" in words
    assert "ParishSoft could not be reached" not in words
    assert kinds({}) == ["source_failing"]
    # A real outage after the key is fixed opens the provider-failure incident
    # while the mismatch one still waits for a good refresh: said as before.
    outage = kinds({"source_tenant_mismatch": NOW, "source_refresh_failed": NOW})
    assert "source_failing" in outage


# ------------------------------ the Backups and Debug logging tiles (slice 5)


def said(tile_id, model, **context):
    """A tile's counted summary row text, without markup."""
    tile = section(whole({"health": model} | context), f'id="{tile_id}"')
    counted = tile.split('class="health-said">', 1)[1].split("</span>", 1)[0]
    return re.sub(r"<[^>]+>", "", counted).strip()


def backup_request(status, *, at=NOW):
    """A Take a backup now request made at ``at``, in page status ``status``."""
    return BackupRequestStatus(
        state="waiting",
        status=status,
        created_at=at,
        held_at=None,
        claimed_at=at,
        finished_at=at,
        failure_kind=None,
    )


def offsite(last_copy_at=NOW, kind="uploaded"):
    """An off-site copy status, as Home shows it."""
    return SimpleNamespace(
        kind=kind,
        message="The newest copy finished.",
        at=NOW,
        last_copy_at=last_copy_at,
    )


def test_the_backup_summary_names_the_first_that_holds():
    """Each Working summary in BACKUP_SUMMARIES's order: a live request,
    running before held before waiting, then the newest backup's age with
    its off-site copy, and finally no backup at all."""
    before = NOW - timedelta(hours=3)
    stamp = NOW.isoformat()
    cases = [
        (
            health(backup_request=backup_request("running"), offsite=offsite()),
            "running",
            f"Backup requested at {stamp} is running",
        ),
        (
            health(backup_request=backup_request("held"), offsite=offsite()),
            "held",
            f"Backup requested at {stamp} is held during the email send",
        ),
        (
            health(backup_request=backup_request("waiting"), offsite=offsite()),
            "waiting",
            f"Backup requested at {stamp} is waiting",
        ),
        (
            health(backup_at=before, offsite=offsite(before)),
            "backup",
            f"Last backup {before.isoformat()}; off-site copy {before.isoformat()}",
        ),
        (
            health(backup_at=None, offsite=offsite()),
            "none",
            "No backup recorded yet",
        ),
    ]
    assert {kind for _, kind, _ in cases} == set(BACKUP_SUMMARIES)
    for model, kind, words in cases:
        assert model.backup_summary.kind == kind
        found = dashboard(model)
        assert found.tiles[2].state == "working", kind
        assert said("health-backups", model) == words, kind
    # A finished, restored or failed request is no longer what the tile
    # waits on; a failed one is the tile's problem instead.
    for status in ("finished", "restored", "failed"):
        model = health(backup_request=backup_request(status), offsite=offsite())
        assert model.backup_summary.kind == "backup", status
    # The off-site part: set up but not copied yet, or (Testing only, as
    # Production makes it the offsite_missing problem) not set up.
    assert said("health-backups", health(offsite=offsite(None))) == (
        f"Last backup {stamp}; no off-site copy yet"
    )
    testing = health(mode="testing")
    assert said("health-backups", testing) == (
        f"Last backup {stamp}; off-site copies not set up"
    )


def test_backup_summary_times_are_browser_local():
    """A request's time is a browser-local clock time (the full date on
    another day); the last backup's age is counted in the browser."""
    model = health(backup_request=backup_request("running"), offsite=offsite())
    tile = section(whole({"health": model}), 'id="health-backups"')
    assert (
        f'<time datetime="{NOW.isoformat()}" data-local-instant data-time-only '
        "data-time-day>" in tile
    )
    tile = section(whole({"health": health(offsite=offsite())}), 'id="health-backups"')
    assert f'<time datetime="{NOW.isoformat()}" data-live-since>' in tile
    assert "UTC" not in tile


def backups_details(body):
    """The Backups tile's details band, up to the next tile."""
    start = body.index('id="health-backups-details"')
    return body[start : body.index('id="health-debug"', start)]


def request_problem(status):
    """The ``backup_request_failed`` problem for a request in ``status``."""
    return Problem("backup_request_failed", at=NOW, detail=status)


def test_the_new_backup_problems_are_warning_items_after_the_others():
    """A backup sealed to another key, a requested backup that ended without
    one and, in Production, no off-site copies follow the other problems as
    Warning items, each with its sentence and link; Take a backup now
    follows them."""
    model = health(
        backup_key_matches=False,
        backup_request=backup_request("not_picked_up"),
        problems=(
            Problem("backup_rpo_breach", at=NOW),
            Problem("backup_key_mismatch"),
            request_problem("not_picked_up"),
            Problem("offsite_missing"),
        ),
    )
    details = backups_details(tiles({"health": model}))
    assert re.findall(r'data-problem="([a-z_]+)"', details) == [
        "backup_rpo_breach",
        "backup_key_mismatch",
        "backup_request_failed",
        "offsite_missing",
    ]
    for kind, link in (
        ("backup_key_mismatch", "/admin/system/integrations/backup_key/"),
        ("backup_request_failed", "stewardship-backup-runbook.md#the-nightly-backup"),
        ("offsite_missing", "/admin/system/integrations/backup/"),
    ):
        item = details[details.index(f'data-problem="{kind}"') :]
        item = item[: item.index("</li>")]
        assert 'data-severity="warning"' in item and link in item, kind
    assert '<li class="notice" data-problem="backup_key_mismatch"' in details
    assert 'data-backup-request="not_picked_up"' in details
    problems_end = details.index("</ul>")
    assert details.index('id="backup-now"') > problems_end
    # Each failed request's own sentence.
    for status, words in (
        ("did_not_finish", "did not finish. Ask the server operator"),
        ("failed", "failed. Ask the server operator"),
    ):
        model = health(
            backup_request=backup_request(status), problems=(request_problem(status),)
        )
        details = backups_details(tiles({"health": model}))
        assert details.count(words) == 1, status
        assert f'data-backup-request="{status}"' in details
        assert "stewardship-backup-runbook.md#checking" in details, status


def test_the_backups_panel_repeats_no_problem_sentence():
    """An overdue backup, a failed off-site copy, a changed key and each
    finding are said once, by the tile's problems; the panel keeps the
    facts and their links."""
    incidents = {"backup_offsite_failed": NOW, "backup_key_changed": NOW}
    model = health(
        backup_key_matches=False,
        backup_request=backup_request("failed"),
        offsite=offsite(kind="failed"),
        incidents=incidents,
        problems=(
            Problem("backup_rpo_breach", at=NOW),
            Problem("backup_offsite_failed", at=NOW),
            Problem("backup_key_changed", at=NOW),
            Problem("backup_key_mismatch"),
            request_problem("failed"),
        ),
    )
    details = backups_details(tiles({"health": model}))
    for words in (
        "No backup has finished since",
        "Copying backups off-site has failed since",
        "different encryption key than the backup before it",
        "sealed to a different key than the backup encryption key",
        "failed. Ask the server operator",
    ):
        assert details.count(words) == 1, words
    for gone in (
        "A required backup is overdue.",
        "An off-site backup copy failed.",
        "A recent backup used a different encryption key",
    ):
        assert gone not in details
    assert "Off-site copy: The newest copy finished." in details
    # The problem's link and the panel's, each to the off-site settings.
    assert details.count("/admin/system/integrations/backup/") == 2
    # Without the incident, a failed copy is still a notice in the panel.
    quiet = backups_details(tiles({"health": health(offsite=offsite(kind="failed"))}))
    assert '<p class="notice">Off-site copy:' in quiet
    # Testing: no off-site copies is a panel fact with its setup link.
    testing = backups_details(tiles({"health": health(mode="testing")}))
    assert "data-problem" not in testing
    assert "Off-site copies are not set up." in testing
    assert "Set up off-site backups" in testing


def test_a_held_request_explains_itself_in_a_toggletip():
    """Why a held request can outlast the send is one click away."""
    model = health(backup_request=backup_request("held"), offsite=offsite())
    details = backups_details(tiles({"health": model}))
    assert "is held while a Family email send is in progress." in details
    tip = " ".join(tip_texts(details))
    assert "can still show for a few minutes after the send ends" in tip
    assert details.count("can still show for a few minutes") == 1
    assert 'aria-describedby="backup-held"' in details


def test_every_backup_request_line_keeps_its_room():
    """No layout shift: the line under Take a backup now has reserved room
    in every state, including none, and its mirror target waits for the
    pointer (data-live-mirror-hold); the held and did_not_finish lines
    render (#1317)."""
    for status, words in (
        ("held", "is held while a Family email send is in progress."),
        ("did_not_finish", "did not complete; the Backups problems above"),
        ("waiting", "is waiting for the server"),
        ("finished", "finished at"),
    ):
        details = backups_details(
            tiles({"health": health(backup_request=backup_request(status))})
        )
        region = details[details.index('id="backup-now"') :]
        region = region[: region.index("</section>")]
        assert words in region, status
        line = re.search(r'<p class="help reserve-lines-wrap backup-line"', region)
        assert line, status
    none = backups_details(tiles({"health": health(backup_request=None)}))
    assert (
        'class="help reserve-lines-wrap backup-line" data-backup-request-none' in none
    )
    assert 'data-live-mirror-target="backup-now-button" data-live-mirror-hold' in none


def test_the_debug_summary_names_the_first_that_holds():
    """Each Working summary in DEBUG_SUMMARIES's order: a set switch with who
    set it, then Testing's debug logging, then Off."""
    stamp = NOW.isoformat()
    on = group_processes(
        [row(debug_logging=True), row("worker", debug_logging=True)], NOW
    )
    cases = [
        (
            health(debug_switch=SET, processes=on, mode="testing"),
            {"debug_actor": "admin@example.org"},
            "switch",
            f"Turned off from this page by admin@example.org at {stamp}",
        ),
        (
            health(processes=on, mode="testing"),
            {},
            "testing",
            "On in 2 services, as expected in Testing",
        ),
        (health(mode="testing"), {}, "off", "Off"),
    ]
    assert {kind for _, _, kind, _ in cases} == set(DEBUG_SUMMARIES)
    for model, context, kind, words in cases:
        assert model.debug_summary.kind == kind
        assert dashboard(model).tiles[3].state == "working", kind
        assert said("health-debug", model, **context) == words, kind
    # Without the address (it could not be read), only the time.
    assert said("health-debug", health(debug_switch=SET)) == (
        f"Turned off from this page at {stamp}"
    )
    tile = section(whole({"health": health(debug_switch=SET)}), 'id="health-debug"')
    assert f'datetime="{stamp}" data-local-instant data-time-only data-time-day' in tile
    # In Production debug logging in effect is the problem's words instead.
    production = health(
        processes=on, problems=(Problem("debug_logging", (Subject("web"),)),)
    )
    assert dashboard(production).tiles[3].state == "warning"


def debug_details(body):
    """The Debug logging tile's details band, up to the next tile."""
    start = body.index('id="health-debug-details"')
    return body[start : body.index('id="health-source-form"', start)]


def test_the_debug_panel_repeats_no_sentence():
    """In Production the problem names the services; who set the switch and
    when, and that it is off everywhere, are said once, by the greyed
    button's reason."""
    on = group_processes([row(debug_logging=True)], NOW)
    problem = Problem("debug_logging", (Subject("web"),))
    details = debug_details(
        tiles({"health": health(processes=on, problems=(problem,))})
    )
    assert details.count("Debug logging is on in Production in") == 1
    assert "Debug logging is on in Web portal." not in details
    assert "Debug logging must be off in Production" not in details
    # Testing lists the services, which no problem names there.
    testing = debug_details(tiles({"health": health(processes=on, mode="testing")}))
    assert "Debug logging is on in Web portal." in testing
    assert "That is expected in Testing" in testing
    # A set switch: who and when once, then that the server still says on.
    context = {"health": health(debug_switch=SET), "debug_actor": "admin@example.org"}
    details = debug_details(tiles(context))
    assert details.count("turned off by admin@example.org at") == 1
    assert "The setting on the server still says “on”." in details
    assert details.count("pk-stewardship debug-off-clear") == 1  # the toggletip
    # Off everywhere: said once, as the region's status line.
    quiet = debug_details(tiles({"health": health()}))
    assert quiet.count("Debug logging is off in every service that reports.") == 1


def test_a_live_request_is_stated_once_in_the_band():
    """The request's progress is one line, under Take a backup now's button
    (#1317): when it was made and its state (and when it started); the
    panel does not repeat it, nor does Apply's answer, the region drawn
    again for the review dialog (#1356), whose status line says only that
    the backup was requested."""
    made = NOW - timedelta(minutes=12)
    started = NOW - timedelta(minutes=2)
    for status in ("waiting", "held", "running"):
        request = BackupRequestStatus(
            state="waiting",
            status=status,
            created_at=made,
            held_at=None,
            claimed_at=started,
            finished_at=None,
            failure_kind=None,
        )
        model = health(backup_request=request, offsite=offsite())
        details = backups_details(tiles({"health": model}))
        assert details.count(f'datetime="{made.isoformat()}"') == 1, status
        assert "A backup was requested at" in details, status
        confirmed = backups_details(
            tiles(
                {
                    "health": model,
                    "backup_now": {"requested": SimpleNamespace(created_at=made)},
                }
            )
        )
        assert confirmed.count(f'datetime="{made.isoformat()}"') == 1, status
        assert details.count("data-backup-request=") == 1, status
        line = confirmed[confirmed.index('id="backup-now-reason"') :]
        assert f'data-backup-request="{status}"' in line[: line.index(">")]
    assert details.count(f'datetime="{started.isoformat()}"') == 1


def test_an_ended_request_is_said_under_the_offered_button():
    """Once the request has ended, the button is offered again and the line
    under it says how it ended; a failure points to the tile's problems,
    which explain it (#1317)."""
    for status, words in (
        ("finished", "The backup requested at"),
        ("restored", "was never run: the system was restored"),
        ("failed", "did not complete; the Backups problems above say what to do"),
        ("not_picked_up", "did not complete; the Backups problems above"),
    ):
        details = backups_details(
            tiles({"health": health(backup_request=backup_request(status))})
        )
        region = details[details.index('id="backup-now"') :]
        region = region[: region.index("</section>")]
        assert 'value="preview"' in region and words in region, status
        panel = details[details.index("</section>") :]
        assert words not in panel, status


def test_the_backup_region_checks_the_status_again_once_answered():
    """Its answer replaces only the region, so the page checks the status at
    once (data-live-recheck) and the progress line follows (#1317)."""
    details = backups_details(tiles({"health": health()}))
    region = details[details.index('id="backup-now"') :]
    assert (
        'data-in-place-region data-live-recheck="system-health"'
        in (region[: region.index(">")])
    )


def test_the_debug_regions_answers_point_where_things_are_said():
    """After turning it off, nothing points to a service list the
    Production panel no longer has; Apply's answer redraws the region with
    its status line; a repeated request, refused in the review dialog
    (#1356), points to that line, which says who and when (no "below" in a
    dialog); the Testing note about the button is left out while the
    switch is set and the button is not drawn."""
    body = debug_off_answer(requested=SimpleNamespace(set_at=NOW))
    assert 'data-debug-status="set"' in body
    assert "details below show when" not in body
    busy = debug_off_answer(refused="busy")
    assert "The line under Turn off debug logging says who did it and when." in busy
    assert "below" not in busy
    assert "Turn off debug logging works here too" in debug_page(mode="testing")
    assert "works here too" not in debug_page(switch=SET, mode="testing")


def test_the_debug_region_checks_the_status_again_once_answered():
    """Its answer replaces only the region, so the page checks the status at
    once (data-live-recheck) and the tile and both buttons follow (#1317)."""
    for body in (
        debug_page(),
        debug_page(switch=SET, debug_off={"requested": SimpleNamespace(set_at=NOW)}),
    ):
        region = body[body.index('id="debug-off"') :]
        assert region[: region.index(">")].endswith(
            'data-in-place-region data-live-recheck="system-health" '
            'aria-labelledby="debug-off-heading"'
        )


# ------------------------------- the page-load and services tiles (slice 6)

BLOCKED = {
    "label": "Initial invitation",
    "subject": "Hi",
    "url": "/e/",
    "blocked": True,
}
RECOMMENDED = {
    "label": "Family welcome",
    "subject": None,
    "url": "/p/",
    "blocked": False,
}


def loaded_page(source_form=0, resave=(), model=None, **context):
    """The dashboard with the page-load tiles read as ``source_form`` and
    ``resave`` (``source_form_summary``'s and ``resave_summary``'s answers),
    as the view draws it."""
    model = model or health()
    resave = list(resave) if isinstance(resave, tuple) else resave
    return whole(
        {
            "health": model,
            "dashboard": dashboard(model, loaded=loaded_states(source_form, resave)),
            "source_form_count": source_form,
            "resave": resave,
            "source_form_tile": source_form_tile(source_form),
            "resave_tile": resave_tile(resave),
        }
        | context
    )


def band(body, tile, following):
    """The tile's details band, up to the next tile ``following`` (or the end)."""
    start = body.index(f'id="health-{tile}-details"')
    end = body.index(f'id="health-{following}"', start) if following else len(body)
    return body[start:end]


def counted_words(body, tile):
    """A tile's counted summary row text, without markup."""
    tile_body = section(body, f'id="health-{tile}"')
    counted = tile_body.split('class="health-said">', 1)[1].split("</span>", 1)[0]
    return re.sub(r"<[^>]+>", "", counted).strip()


def test_the_source_form_summary_names_the_first_that_holds():
    """Each summary in SOURCE_FORM_SUMMARIES's order, with its state, which
    loaded_states takes from the same choice."""
    cases = [
        (None, "unchecked", "warning", "Could not be checked"),
        (3, "families", "warning", "3 Families cannot open the form"),
        (1, "families", "warning", "1 Family cannot open the form"),
        ("no_data", "no_data", "working", "No ParishSoft data yet"),
        ("no_campaign", "no_campaign", "working", "No current campaign"),
        (0, "none", "working", "Every Family can open the form"),
    ]
    assert {kind for _, kind, _, _ in cases} == set(SOURCE_FORM_SUMMARIES)
    for answer, kind, state, words in cases:
        summary = source_form_tile(answer)
        assert (summary.kind, summary.state) == (kind, state), answer
        assert loaded_states(answer, []).get("source-form", "working") == state
        assert counted_words(loaded_page(answer), "source-form") == words, answer


def test_the_resave_summary_names_the_first_that_holds():
    """Each summary in RESAVE_SUMMARIES's order: a blocked email is an Error
    named with how many other items follow it."""
    cases = [
        (None, "unchecked", "warning", "Could not be checked"),
        (
            [RECOMMENDED, BLOCKED, RECOMMENDED],
            "blocked",
            "error",
            "1 email cannot be sent until it is fixed and 2 more",
        ),
        (
            [BLOCKED, BLOCKED],
            "blocked",
            "error",
            "2 emails cannot be sent until they are fixed",
        ),
        ([RECOMMENDED, RECOMMENDED], "recommended", "warning", "2 to re-save"),
        ("no_campaign", "no_campaign", "working", "No current campaign"),
        ([], "none", "working", "Nothing to re-save"),
    ]
    assert {kind for _, kind, _, _ in cases} == set(RESAVE_SUMMARIES)
    for answer, kind, state, words in cases:
        summary = resave_tile(answer)
        assert (summary.kind, summary.state) == (kind, state), answer
        assert loaded_states(0, answer).get("resave", "working") == state
        assert counted_words(loaded_page(resave=answer), "resave") == words, kind


def test_a_source_form_finding_is_its_tiles_problem_item_with_the_link():
    """Families listed, or a check that could not run, are the Warning
    tile's problem item, linking the list; otherwise a plain line with no
    link, since the list would be empty (conditional fields)."""
    link = 'href="/admin/system/source-form/"'
    for answer in (2, None):
        details = band(loaded_page(answer), "source-form", "resave")
        assert 'data-problem="source_form" data-severity="warning"' in details
        assert link in details
        assert 'aria-controls="source-form-tip"' in details
    for answer in (0, "no_data", "no_campaign"):
        details = band(loaded_page(answer), "source-form", "resave")
        assert "data-problem" not in details and link not in details, answer
        assert 'aria-controls="source-form-tip"' in details


def test_a_resave_finding_is_its_tiles_problem_item_with_the_list():
    """A blocked email is an error notice, other items a warning; each is
    followed by the linked list and links Pages and emails, which a Working
    tile leaves out."""
    catalog = 'href="/admin/campaign/content/"'
    error = band(loaded_page(resave=[BLOCKED, RECOMMENDED]), "resave", "version")
    assert (
        'class="notice notice-error" data-problem="resave" data-severity="error"'
        in error
    )
    assert "1 email cannot be sent until it is fixed" in error
    assert "1 other saved page or email should be re-saved." in error
    assert error.count('href="/e/"') == 1 and error.count('href="/p/"') == 1
    assert catalog in error
    warning = band(loaded_page(resave=[RECOMMENDED]), "resave", "version")
    assert 'class="notice" data-problem="resave" data-severity="warning"' in warning
    assert catalog in warning
    unchecked = band(loaded_page(resave=None), "resave", "version")
    assert 'data-severity="warning"' in unchecked and catalog in unchecked
    for answer in ([], "no_campaign"):
        working = band(loaded_page(resave=answer), "resave", "version")
        assert "data-problem" not in working and catalog not in working
        assert "resave-list" not in working
        assert 'aria-controls="resave-tip"' in working


def test_the_page_load_tips_and_about_say_why_the_tiles_do_not_follow_the_poll():
    """A toggletip beside each sentence, and "About this page" explains what
    keeps a Family from the form."""
    page_body = loaded_page(2, [RECOMMENDED])
    assert page_body.count("checked when the page opens, not every 10 seconds") == 2
    from pathlib import Path

    template = (
        Path(__file__).parents[2]
        / "src/parishkit/stewardship/accounts/templates/stewardship/system-health.html"
    ).read_text()
    assert '<dt>{% translate "Families the form cannot open" %}</dt>' in template
    assert "the next ParishSoft refresh" in template


def test_the_version_summary_names_the_first_that_holds():
    """Each Working summary in VERSION_SUMMARIES's order."""
    cases = [
        (health(), "version", "Version 1.4.2, Production, all services running"),
        (
            health(mode="testing"),
            "version",
            "Version 1.4.2, Testing, all services running",
        ),
        (health(versions=()), "none", "No service has reported its version"),
    ]
    assert {kind for _, kind, _ in cases} == set(VERSION_SUMMARIES)
    for model, kind, words in cases:
        assert model.version_summary.kind == kind
        assert said("health-version", model) == words, words


def test_the_version_panel_repeats_no_problem():
    """Different versions and a database that does not match are the tile's
    problems, said once; the panel keeps the facts."""
    model = health(
        versions=("1.4.1", "1.4.2"),
        schema_current=False,
        problems=(Problem("versions_differ"), Problem("schema_mismatch")),
    )
    body = whole({"health": model})
    details = band(body, "version", None)
    assert details.count("The services run different versions") == 1
    assert details.count("The database does not match") == 1
    assert 'data-problem="versions_differ"' in details
    assert 'data-problem="schema_mismatch"' in details
    assert "The database matches" not in details
    assert "All services run version" not in details
    assert "Mode:" in details and "<caption>Services</caption>" in details
    healthy = band(whole({"health": health()}), "version", None)
    assert "All services run version 1.4.2." in healthy
    assert "The database matches the version that is running." in healthy
    assert "data-problem" not in healthy
    # The polled copy is the same panel.
    region = page({"health": model})[0]
    copy = region.split('data-live-mirror="health-version-panel">', 1)[1]
    assert "The services run different versions" not in copy.split("</template>")[0]


def test_accept_this_change_once_draws_its_offer_once():
    """An open refusal's offer is its one button, whatever the sign-in's age
    (#1547): no step-up copy, and the polled copy leaves its CSRF token
    empty."""
    body = accept_page()
    offer = section(body, 'id="accept-change"')
    assert offer.count('value="accept_preview"') == 1
    assert "accept-when" not in offer and "For security" not in offer
    model = health(refused_at=NOW, refused_counts=FAILED)
    region = page({"health": model, "refused_attempt": REFUSED_ATTEMPT})[0]
    copy = region.split('data-live-mirror="accept-offer">', 1)[1].split("</template>")[
        0
    ]
    assert 'value="accept_preview"' in copy
    assert 'name="csrfmiddlewaretoken" value=""' in copy


def test_no_page_or_fragment_judges_the_sign_in():
    """System health never reads the sign-in's age (#1547)."""
    from pathlib import Path

    from parishkit.stewardship.accounts import system_health_views

    source = Path(system_health_views.__file__).read_text()
    assert "freshness" not in source and 'context["fresh"]' not in source
    template = (
        Path(system_health_views.__file__).parent
        / "templates/stewardship/system-health.html"
    ).read_text()
    assert "data-health-fresh" not in template


@pytest.mark.parametrize(
    ("kind", "tile", "link", "following"),
    [
        ("versions_differ", "version", "See Version", None),
        ("mail_provider_unavailable", "mail", "See Mail sender", "source"),
    ],
)
def test_a_sentence_links_its_tile_only_off_the_page(kind, tile, link, following):
    """#1385: inside its own tile the sentence draws no link to that tile;
    Home, which has no ``health``, keeps it."""
    problem = Problem(kind, at=NOW)
    model = health(problems=(problem,), incidents={kind: NOW})
    details = band(whole({"health": model}), tile, following)
    assert f'data-problem="{kind}"' in details
    assert link not in details
    home = render_to_string(
        "stewardship/system-health-problem.html", {"problem": problem}
    )
    assert f'#health-{tile}">{link}</a>' in home


# One problem of every registry kind, as Home lists them (#1214).
MAILER = Subject("mail-dispatch", "mail")
HOME_SUBJECTS = {
    "sender_halted": (MAILER,),
    "sender_outage": (MAILER,),
    "not_running": (Subject("worker", "source"),),
    "not_reported": (Subject("scheduler"),),
    "debug_logging": (Subject("web", "main"),),
}


def home_line(kind):
    """Home's line for one problem of ``kind``, as Home draws it."""
    problem = Problem(
        kind,
        subjects=HOME_SUBJECTS.get(kind, ()),
        at=NOW,
        count=1 if kind == "refusal_unrecorded" else 0,
    )
    return problem, render_to_string(
        "stewardship/system-health-home-line.html", {"problem": problem}
    )


@pytest.mark.parametrize("kind", sorted(REGISTRY))
def test_a_home_line_shows_its_state_and_links_its_tile_once(kind):
    """Each Home line shows the registry's icon and state word beside the
    page's sentence, and links its problem's tile exactly once: by the
    sentence's own link when it has one, else by "See … in System health"."""
    problem, line = home_line(kind)
    severity = REGISTRY[kind][1]
    assert problem.severity == severity
    assert f'data-severity="{severity}"' in line
    assert f"level-icon-{severity}" in line
    word = {"warning": "Warning", "error": "Error"}[severity]
    assert f'<span class="status-word">{word}</span>' in line
    assert ("notice-error" in line) is (severity == "error")
    anchor = f'href="/admin/system/health/#health-{REGISTRY[kind][0]}"'
    # An unreadable backup key keeps System health from opening, so its
    # line links no tile at all.
    assert line.count(anchor) == (kind != "backup_key_unchecked"), line
    assert line.count('href="/admin/system/health/"') == 0
    own = f"See {problem.tile_name} in System health" in line
    assert own is problem.tile_link


def test_home_words_and_tiles_for_a_split_process_problem():
    """A stopped installer alone is a warning; with a mail sender the line
    links the Mail tile, its first, and is an error."""
    installer = Problem("not_running", (Subject("config-installer"),), NOW)
    assert (installer.severity, installer.word, installer.tile) == (
        "warning",
        "Warning",
        "version",
    )
    both = replace(installer, subjects=(MAILER, Subject("config-installer")))
    assert (both.severity, both.tile, both.tile_name) == ("error", "mail", "Mail")


def refresh_block(**context):
    """Home's ParishSoft refresh block with a failed and a late full refresh."""
    status = FullRefreshStatus(
        NOW - timedelta(hours=8),
        NOW - timedelta(hours=1),
        False,
        failed_task_id=uuid4(),
        overdue_at=NOW - timedelta(minutes=30),
        out_of_date=True,
        late_minutes=30,
    )
    return render_to_string(
        "stewardship/full-refresh-status.html",
        {
            "status": status,
            "can_view_task": True,
            "can_refresh": True,
            "refresh_key": "key",
        }
        | context,
    )


def test_home_shows_a_listed_refresh_problem_once_and_keeps_its_links():
    """#1214: with ``source_failed`` and ``source_late`` listed, Home's
    refresh block leaves both notices out but keeps "See what went wrong"
    and one Run a full refresh now. Without them both notices stay."""
    listed = refresh_block(failed_listed=True, problems_listed=True, keep_action=True)
    assert "notice-error" not in listed
    assert "did not finish" not in listed and "minutes late" not in listed
    assert "See what went wrong" in listed
    assert listed.count("Run a full refresh now") == 1
    # Only the failure listed (the late refresh is not): the late notice
    # keeps the button, and the failure's plain line adds none.
    failed = refresh_block(failed_listed=True, keep_action=True)
    assert failed.count("notice-error") == 1 and "minutes late" in failed
    assert failed.count("Run a full refresh now") == 1
    plain = refresh_block()
    assert plain.count("notice-error") == 2 and "did not finish" in plain
    assert plain.count("Run a full refresh now") == 1


def test_only_a_listed_failure_gives_the_off_site_line_way():
    """``health_listed``: each notice gives way only to its own listed
    problem, and the off-site line only while it reports the failure."""
    from parishkit.stewardship.accounts.admin_dashboard import health_listed
    from parishkit.stewardship.accounts.backup_destination import OffsiteStatus

    def offsite(kind):
        """An off-site line of ``kind``."""
        return OffsiteStatus(kind, "Words.", NOW, NOW, None)

    listed = (
        Problem("source_failed", at=NOW),
        Problem("source_late", at=NOW),
        Problem("backup_offsite_failed", at=NOW),
    )
    assert health_listed(listed, offsite("failed")) == {
        "failed": True,
        "late": True,
        "offsite": True,
        "offsite_unset": False,
    }
    # The incident is open, but the newest copy went through: the line no
    # longer reports a failure, so it stays.
    for kind in ("uploaded", "disabled", "none"):
        assert health_listed(listed, offsite(kind))["offsite"] is False, kind
    assert health_listed(listed, None)["offsite"] is False
    # A failing off-site line with no listed problem stays too.
    assert health_listed((), offsite("failed")) == {
        "failed": False,
        "late": False,
        "offsite": False,
        "offsite_unset": False,
    }
    # Off-site copies not set up (Production): the invitation gives way.
    assert health_listed((Problem("offsite_missing"),), None)["offsite_unset"]


def home_page(dashboard):
    """Home's page body as the index view draws it for ``dashboard``."""
    from parishkit.stewardship.accounts.backup_destination import OffsiteStatus

    status = FullRefreshStatus(
        NOW - timedelta(days=1), NOW - timedelta(hours=1), False, failed_task_id=uuid4()
    )
    return render_to_string(
        "stewardship/home.html",
        {
            "admin_chrome": {"admin": True},
            "suppress_admin_session_chrome": True,
            "dashboard": {
                "full_refresh": status,
                "can_view_task": True,
                "can_refresh": True,
                "refresh_key": "key",
                "offsite": OffsiteStatus(
                    "failed", "Copying the newest backup failed.", NOW, NOW, None
                ),
            }
            | dashboard,
        },
    )


def test_home_keeps_both_notices_for_a_viewer_without_system_health():
    """A viewer who may not open System health gets no lines and no
    ``health_listed``, so the failed refresh and the failed off-site copy
    keep their own notices; a viewer with the lines sees each once."""
    other = home_page({})
    assert "data-home-health" not in other
    assert "did not finish." in other
    assert "Copying the newest backup failed." in other
    assert other.count("Run a full refresh now") == 1
    problems = (
        Problem("source_failed", at=NOW - timedelta(hours=1)),
        Problem("backup_offsite_failed", at=NOW),
    )
    admin = home_page(
        {
            "health_problems": problems,
            "health_listed": {"failed": True, "late": False, "offsite": True},
        }
    )
    assert admin.count("did not finish.") == 1
    assert "Copying the newest backup failed." not in admin
    assert admin.count("Run a full refresh now") == 1
    assert admin.count("Off-site backups (Google Drive)") == 1


@pytest.mark.parametrize(
    ("status", "backup_after", "listed"),
    [
        # A newer finished backup clears a request that failed or did not
        # finish: backups work again.
        ("failed", True, False),
        ("did_not_finish", True, False),
        ("failed", False, True),
        ("did_not_finish", False, True),
        # A request never picked up stays: a good nightly backup does not
        # prove the server checks for requests.
        ("not_picked_up", True, True),
        ("not_picked_up", False, True),
    ],
)
def test_when_a_failed_request_clears(status, backup_after, listed):
    """B1 of #1461: the tiles table and the backups panel's rule."""
    backup_at = NOW + timedelta(hours=1) if backup_after else NOW - timedelta(hours=1)
    found = problems(backup_request=failed_request(status), backup_at=backup_at)
    assert found == (["backup_request_failed"] if listed else []), status


def test_a_watch_stops_when_only_problems_waiting_cannot_clear_remain():
    """``system health --watch`` ends with only off-site copies not set up or
    a failed request left, and keeps watching while anything else holds."""
    from parishkit.stewardship.system_health import WAIT_CANNOT_CLEAR

    assert {"offsite_missing", "backup_request_failed"} == WAIT_CANNOT_CLEAR
    assert health().terminal
    stuck = (Problem("offsite_missing"), request_problem("not_picked_up"))
    assert health(problems=stuck).terminal
    for other in (Problem("backup_rpo_breach", at=NOW), Problem("backup_key_mismatch")):
        assert not health(problems=(*stuck, other)).terminal, other.kind
