"""The System health read model's pure parts (ADM-13 PR 2, #530).

Grouping status rows into processes (a restart's old row never shows as not
running, web processes are counted), the problems list, and the document
projection, without a database.
"""

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.source.data_age import Connection
from parishkit.stewardship.source.refresh_status import FullRefreshStatus
from parishkit.stewardship.system_health import (
    BACKUP_SUMMARIES,
    DEBUG_SUMMARIES,
    INCIDENTS,
    LOADED_TILES,
    MAIL_SUMMARIES,
    REGISTRY,
    SOURCE_SUMMARIES,
    TILES,
    AcceptPreview,
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
    page_findings,
    parse_loaded,
    tile_parts,
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


def page(context, *, request=None):
    """The page's dashboard as the view draws it, without the Admin chrome.

    Returns ``(region, tiles)``: the polled status region (the overall line
    and the copies the poll carries), then the seven tiles with their
    details. ``context`` is the view's; its dashboard is built from
    ``health`` as the view builds it, unless given.
    """
    context = {"pause": None, "poll_interval": 10000} | context
    model = context["health"]
    context.setdefault(
        "dashboard",
        dashboard(
            model,
            findings=page_findings(
                model, workgroup_missing=context.get("workgroup_missing")
            ),
        ),
    )
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


def test_page_findings_raise_their_tile_to_a_warning():
    """Page notices not yet in the shared list still open their tile."""
    failed = SimpleNamespace(status="not_picked_up")
    assert page_findings(health()) == ("offsite_missing",)
    offsite = SimpleNamespace(kind="succeeded", message="", at=NOW, last_copy_at=NOW)
    assert page_findings(health(offsite=offsite)) == ()
    assert page_findings(health(mode="testing")) == ()
    found = page_findings(
        health(backup_key_matches=False, backup_request=failed),
        workgroup_missing="No reminders",
    )
    assert found == (
        "workgroup_missing",
        "backup_key_mismatch",
        "backup_request_failed",
        "offsite_missing",
    )
    tiles = {tile.key: tile for tile in dashboard(health(), findings=found).tiles}
    assert tiles["source"].state == "warning"
    assert tiles["source"].top_finding == "workgroup_missing"
    assert tiles["backups"].state == "warning" and tiles["backups"].more == 2
    # A counted problem names the summary ahead of a finding.
    tiles = {
        tile.key: tile
        for tile in dashboard(
            health(problems=(Problem("backup_rpo_breach", at=NOW),)), findings=found
        ).tiles
    }
    assert tiles["backups"].state == "error"
    assert tiles["backups"].top_finding is None


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
    # Each problem in its one tile, and the Production finding that off-site
    # copies are not set up as the Backups tile's item: the line counts the
    # tiles.
    assert body.count("data-problem=") == len(codes) + 1
    assert 'data-problem="offsite_missing"' in body
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
    assert f'href="/admin/mail/outgoing/{message}/">See the email</a>' in one
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
        body = whole({"health": health(backup_request=request)})
        assert words in body, code
        assert f'data-backup-request="{code}"' in body
    body = whole({"health": health()})
    assert "Its size is 3.0" in body and "taken by version 1.4.2" in body


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
    """The button posts the preview step in place; help says who clears it."""
    body = debug_page()
    assert 'value="debug_off_preview"' in body
    assert 'data-in-place="debug-off-preview"' in body
    assert 'action="/admin/system/health/#debug-off"' in body
    # The polled copy carries the same enabled button for the mirror.
    assert '<template data-live-mirror="debug-off-button">' in body
    assert "pk-stewardship debug-off-clear" in body
    assert "those whose server setting is still on log in debug form again" in body
    # In Production the debug_logging problem says what to do; the panel
    # does not say it again.
    assert "Debug logging must be off in Production" not in body


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
    assert "Debug logging was turned off by admin@example.org at" in quiet
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
    region = section(body, 'id="debug-off"')
    assert "data-live-mirror-target" not in region
    done = debug_page(
        switch=DebugSwitchStatus(set_at=NOW, cleared_at=None, cleared_by=None),
        debug_off={"requested": SimpleNamespace(set_at=NOW), "created": True},
    )
    assert "data-debug-off-set" in done and "Debug logging turned off at" in done
    for refused, words in (
        ("reauthenticate", "For security, turning off debug logging needs a Google"),
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
        ("reauthenticate", "reauthenticate", "For security, accepting this change"),
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
    """Testing with the switch set: the button posts the preview in place,
    and the polled copy carries it. Otherwise it is greyed with why."""
    body = debug_page(switch=SET, debug=False, mode="testing")
    assert 'value="debug_allow_preview"' in body
    assert 'data-in-place="debug-allow-preview"' in body
    assert body.count('value="debug_allow_preview"') == 2  # region and mirror
    assert "In Testing, Allow debug logging again on this page clears" in body
    production = debug_page(switch=SET, debug=False)
    assert 'value="debug_allow_preview"' not in production
    assert 'disabled aria-describedby="debug-allow-reason"' in production
    assert "In Production only the server operator can clear" in production
    unset = debug_page(mode="testing")
    assert 'value="debug_allow_preview"' not in unset
    assert "The debug-off switch is not set." in unset


def test_the_allow_preview_result_and_refusals_read_in_the_region():
    """Allow debug logging again answers inside #debug-off, in its own words."""
    preview = DebugAllowPreview(key="k-2", set_at=NOW)
    body = debug_page(
        switch=SET, mode="testing", debug_off={"allow": True, "preview": preview}
    )
    for words in (
        "This clears the debug-off switch set at",
        "never turns debug logging on in a service started without it",
        "Confirm: allow debug logging again",
    ):
        assert words in body, words
    assert 'name="key" value="k-2"' in body
    assert 'value="debug_allow_confirm"' in body
    assert 'value="debug_allow_cancel"' in body
    # The region alone: the tile's panel content follows it, in its own
    # mirror target.
    start = body.index('id="debug-off"')
    region = body[start : body.index("</section>", start)]
    assert "data-live-mirror-target" not in region
    assert "Confirm: turn off debug logging" not in body
    cleared = DebugSwitchStatus(
        set_at=NOW - timedelta(hours=1), cleared_at=NOW, cleared_by="administrator"
    )
    done = debug_page(
        switch=cleared,
        mode="testing",
        debug_off={"allow": True, "allowed": cleared, "created": True},
    )
    assert "data-debug-allowed" in done and "Debug-off switch cleared at" in done
    for refused, words in (
        ("reauthenticate", "allowing debug logging again needs a Google sign-in"),
        ("busy", "The debug-off switch is no longer set"),
        ("production", "In Production only the server operator can clear"),
        ("denied", "The server refused to allow debug logging again"),
        ("invalid", "That request could not be read."),
    ):
        answer = debug_page(debug_off={"allow": True, "refused": refused})
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
    model = health(refused_at=NOW, refused_counts=FAILED, problems=listed)
    (_, source, *_) = dashboard(model, findings=("workgroup_missing",)).tiles
    assert [part.problem.kind for part in source.listed] == [
        "source_late",
        "source_tenant_mismatch",
        "source_retention_failing",
    ]
    assert source.refusal.problem == DROP_PROBLEM
    details = source_details(
        tiles(
            {
                "health": model,
                "workgroup_missing": "No reminders",
                "refused_attempt": REFUSED_ATTEMPT,
            }
        )
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
        found = dashboard(model, findings=page_findings(model))
        assert found.tiles[2].state == "working", kind
        assert said("health-backups", model) == words, kind
    # A finished, restored or failed request is no longer what the tile
    # waits on; a failed one is the tile's finding instead.
    for status in ("finished", "restored", "failed"):
        model = health(backup_request=backup_request(status), offsite=offsite())
        assert model.backup_summary.kind == "backup", status
    # The off-site part: set up but not copied yet, or (Testing only, as
    # Production makes it a finding) not set up.
    assert said("health-backups", health(offsite=offsite(None))) == (
        f"Last backup {stamp}; no off-site copy yet"
    )
    testing = health(mode="testing")
    assert page_findings(testing) == ()
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


def test_the_backup_findings_are_warning_items_after_the_problems():
    """A backup sealed to another key, a requested backup that ended without
    one and, in Production, no off-site copies follow the shared problems
    as Warning items, each with its sentence and link; Take a backup now
    follows them."""
    model = health(
        backup_key_matches=False,
        backup_request=backup_request("not_picked_up"),
        problems=(Problem("backup_rpo_breach", at=NOW),),
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
        model = health(backup_request=backup_request(status))
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
    # Off everywhere: said once, as the greyed button's reason.
    quiet = debug_details(tiles({"health": health()}))
    assert quiet.count("Debug logging is off in every service that reports.") == 1


def test_a_live_request_is_stated_once_in_the_band():
    """The request's progress is one line, under Take a backup now's button
    (#1317): when it was made and its state (and when it started); the
    panel does not repeat it, and the confirmation points to the line."""
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
        assert (
            "Backup requested. The line under the button follows its progress"
            in confirmed
        )
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
    Production panel no longer has; a repeated request points to the greyed
    button, which says who and when; the Testing note about the button is
    left out while the switch greys it."""
    body = debug_page(debug_off={"requested": SimpleNamespace(set_at=NOW)})
    assert "Each service stops logging in debug form within about a minute." in body
    assert "details below show when" not in body
    busy = debug_page(switch=SET, debug_off={"refused": "busy"})
    assert "The greyed button below says who did it and when." in busy
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
