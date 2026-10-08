"""Full-refresh status: a later success clears the failure banner and its link."""

import re
from contextlib import contextmanager
from dataclasses import fields
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.campaigns import go_live_sequencing
from parishkit.stewardship.source import refresh_status, send_hold
from parishkit.stewardship.source.data_age import (
    FACTS_COLUMNS,
    FACTS_SQL,
    Connection,
    SourceFacts,
)
from parishkit.stewardship.source.refresh_status import (
    OUTCOMES_SQL,
    FullRefreshStatus,
)

# The real reader, kept before the autouse fixture below replaces it.
real_facts = refresh_status._facts

EARLY = datetime(2026, 9, 28, 6, 3, tzinfo=UTC)
LATE = datetime(2026, 9, 28, 11, 15, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_data_age(monkeypatch):
    """No snapshot facts and no overdue slot unless a test sets them (#510)."""
    monkeypatch.setattr(refresh_status, "_facts", lambda row: SourceFacts())
    monkeypatch.setattr(refresh_status, "overdue_full_slot", lambda now, **_: None)
    monkeypatch.setattr(send_hold, "allowance_applies", lambda *args, **kw: False)
    # No go-live is in progress in these no-database tests (#462).
    monkeypatch.setattr(go_live_sequencing, "held_until", lambda overdue: overdue)
    monkeypatch.setattr(go_live_sequencing, "refreshes_held", lambda instant: False)
    monkeypatch.setattr(go_live_sequencing, "last_hold_end", lambda instant: None)


def status_from(monkeypatch, row, schedule=None, now=None):
    """Run full_refresh_status against one canned database row."""

    class Cursor:
        def execute(self, sql, params=None):
            self.sql = sql

        def fetchone(self):
            return row

    class Connection:
        @contextmanager
        def cursor(self):
            yield Cursor()

    monkeypatch.setattr(refresh_status, "connection", Connection())
    return refresh_status.full_refresh_status(schedule, now)


def test_later_success_clears_the_failure_and_its_link(monkeypatch):
    """The banner disappears once a newer full load succeeds."""
    status = status_from(monkeypatch, (LATE, EARLY, False, uuid4()))
    assert status.failed_at is None and status.failed_task_id is None


def test_newer_failure_keeps_its_task_link(monkeypatch):
    """A failure after the last success is shown with its task."""
    task = uuid4()
    status = status_from(monkeypatch, (EARLY, LATE, False, task))
    assert status.failed_at == LATE and status.failed_task_id == task


def test_banner_links_to_the_failed_task_not_the_background_list():
    """Admins land on the failing run's details, not the generic list."""
    task = uuid4()
    html = render_to_string(
        "stewardship/full-refresh-status.html",
        {"status": FullRefreshStatus(EARLY, LATE, False, task), "can_view_task": True},
    )
    assert f"/admin/system/background/{task}/" in html
    assert "See Background work" not in html


def test_banner_omits_the_link_for_users_who_cannot_open_tasks():
    """Staff see the failure notice but no link to a page they cannot open."""
    task = uuid4()
    html = render_to_string(
        "stewardship/full-refresh-status.html",
        {"status": FullRefreshStatus(EARLY, LATE, False, task), "can_view_task": False},
    )
    assert "notice-error" in html
    assert f"/admin/system/background/{task}/" not in html


def test_banner_absent_without_a_failure():
    """No failure means no error notice at all."""
    html = render_to_string(
        "stewardship/full-refresh-status.html",
        {"status": FullRefreshStatus(LATE, None, False)},
    )
    assert "notice-error" not in html


DELTA_OK = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)
DELTA_BAD = datetime(2026, 9, 28, 15, 15, tzinfo=UTC)
NOW = datetime(2026, 9, 28, 16, 5, tzinfo=UTC)
SCHEDULE = {
    "timezone": "America/New_York",
    "nightly_time": "02:00",
    "frequency": "daily",
    "full_refresh_times": ("02:00",),
    "delta_refresh": "quarter_hour",
}

# A page-issued manual refresh request key.
KEY = uuid4()


def test_healthy_deltas_are_reported_beside_a_failed_full_reload(monkeypatch):
    """A failed nightly reload with working incremental updates says so."""
    status = status_from(monkeypatch, (EARLY, LATE, False, uuid4(), DELTA_OK, None))
    assert status.failed_at == LATE
    assert status.deltas_healthy is True
    html = render_to_string(
        "stewardship/full-refresh-status.html",
        {
            "status": status,
            "can_view_task": True,
            "can_refresh": True,
            "refresh_key": KEY,
        },
    )
    assert "15-minute updates are working" in html
    assert "Ministry rosters, ministries and giving" in html
    # The one-click action posts the refresh page's own request shape.
    assert 'method="post" action="/admin/parish/parishsoft-refresh/"' in html
    assert f'name="request_key" value="{KEY}"' in html


def test_failed_deltas_after_the_last_success_are_unhealthy(monkeypatch):
    """An incremental failure newer than every success marks updates failing."""
    status = status_from(
        monkeypatch, (EARLY, LATE, False, uuid4(), DELTA_OK, DELTA_BAD)
    )
    assert status.deltas_healthy is False


def test_a_later_success_clears_an_incremental_failure(monkeypatch):
    """A newer incremental or full success supersedes an older delta failure."""
    status = status_from(monkeypatch, (EARLY, None, False, None, DELTA_BAD, DELTA_OK))
    assert status.delta_failed_at is None and status.deltas_healthy is True


def test_next_scheduled_full_reload_uses_the_parish_time(monkeypatch):
    """Daily reloads fall at the next local nightly time, DST-resolved."""
    from parishkit.stewardship.source import refresh_status as module

    class Cursor:
        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return (EARLY, LATE, False, None, DELTA_OK, None)

    class Connection:
        @contextmanager
        def cursor(self):
            yield Cursor()

    monkeypatch.setattr(module, "connection", Connection())
    status = module.full_refresh_status(SCHEDULE, NOW)
    # 02:00 EDT on 9/29 is 06:00 UTC.
    assert status.next_full_at == datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
    assert status.frequency == "daily"
    assert status.nightly_only and status.has_deltas
    # With several times a day the next one is the next listed time (#465).
    several = SCHEDULE | {"full_refresh_times": ("02:00", "14:00")}
    status = module.full_refresh_status(several, NOW)
    assert status.next_full_at == datetime(2026, 9, 28, 18, 0, tzinfo=UTC)
    assert not status.nightly_only


def test_next_full_for_hourly_and_quarter_hour():
    """Frequent schedules fall on the next UTC hour or quarter hour."""
    from parishkit.stewardship.source.cadence import next_full_at

    assert next_full_at(
        now=NOW, timezone="America/New_York", nightly_time="02:00", frequency="hourly"
    ) == datetime(2026, 9, 28, 17, 0, tzinfo=UTC)
    assert next_full_at(
        now=NOW,
        timezone="America/New_York",
        nightly_time="02:00",
        frequency="quarter_hour",
    ) == datetime(2026, 9, 28, 16, 15, tzinfo=UTC)


def test_quarter_hour_schedule_has_no_separate_incremental_status():
    """When every run is full, no incremental line is shown."""
    status = FullRefreshStatus(
        EARLY, LATE, False, None, DELTA_OK, None, "quarter_hour", NOW
    )
    assert status.deltas_healthy is None
    html = render_to_string(
        "stewardship/full-refresh-status.html",
        {"status": status, "can_refresh": True},
    )
    assert "15-minute updates" not in html
    assert "The most recent full ParishSoft reload" in html


def test_refresh_page_does_not_link_to_itself():
    """The refresh page is the action, so the banner omits the link there."""
    html = render_to_string(
        "stewardship/full-refresh-status.html",
        {
            "status": FullRefreshStatus(EARLY, LATE, False, None, DELTA_OK),
            "can_refresh": True,
            "refresh_key": KEY,
            "on_refresh_page": True,
        },
    )
    assert "Run a full refresh now" not in html


def test_staff_without_refresh_capability_get_no_run_link():
    """Only Administrators are offered the manual full refresh."""
    html = render_to_string(
        "stewardship/full-refresh-status.html",
        {"status": FullRefreshStatus(EARLY, LATE, False, None, DELTA_OK)},
    )
    assert "Run a full refresh now" not in html


def test_a_healthy_status_still_offers_the_run_button():
    """Admins can start a full refresh without waiting for a failure."""
    html = render_to_string(
        "stewardship/full-refresh-status.html",
        {
            "status": FullRefreshStatus(LATE, None, False, None, DELTA_OK),
            "can_refresh": True,
            "refresh_key": KEY,
            "idle_action": True,
        },
    )
    assert "notice-error" not in html
    assert "Run a full refresh now" in html


def test_home_page_offers_the_button_only_beside_a_failure():
    """Without a failure the dashboard keeps its own single Refresh now link."""
    html = render_to_string(
        "stewardship/full-refresh-status.html",
        {
            "status": FullRefreshStatus(LATE, None, False, None, DELTA_OK),
            "can_refresh": True,
            "refresh_key": KEY,
        },
    )
    assert "Run a full refresh now" not in html


def test_several_daily_times_name_the_most_recent_reload():
    """With more than one full refresh a day the notice is not "the nightly" one."""
    status = FullRefreshStatus(
        EARLY, LATE, False, None, DELTA_OK, None, "daily", NOW, ("02:00", "14:00")
    )
    html = render_to_string(
        "stewardship/full-refresh-status.html", {"status": status, "can_refresh": True}
    )
    assert "The most recent full ParishSoft reload" in html
    assert "The nightly full ParishSoft reload" not in html


def test_hourly_deltas_are_named_and_off_deltas_are_not_reported():
    """The incremental line follows the configured delta cadence (#465)."""
    hourly = FullRefreshStatus(
        EARLY, LATE, False, None, DELTA_OK, None, "daily", NOW, ("02:00",), "hourly"
    )
    assert hourly.deltas_healthy is True
    html = render_to_string(
        "stewardship/full-refresh-status.html", {"status": hourly, "can_refresh": True}
    )
    assert "Last hourly update:" in html and "hourly updates are working" in html
    assert "15-minute" not in html
    off = FullRefreshStatus(
        EARLY, LATE, False, None, DELTA_OK, None, "daily", NOW, ("02:00",), "off"
    )
    assert off.deltas_healthy is None and not off.has_deltas
    html = render_to_string(
        "stewardship/full-refresh-status.html", {"status": off, "can_refresh": True}
    )
    assert "update" not in html.split("notice-error")[0]


def page(status, **context):
    """Render the shared status block for ``status``."""
    return render_to_string(
        "stewardship/full-refresh-status.html", {"status": status} | context
    )


def test_data_as_of_moves_with_a_changed_quick_update_and_names_the_full(monkeypatch):
    """A quick update that changed records moves "data as of" (#510).

    The last full refresh is named beside it, and both are browser-local
    instants, never labeled UTC.
    """
    facts = SourceFacts(full_started_at=EARLY, changed_delta_at=LATE)
    monkeypatch.setattr(refresh_status, "_facts", lambda row: facts)
    status = status_from(monkeypatch, (EARLY, None, False, None, LATE, None))
    assert status.data_as_of == LATE and status.full_started_at == EARLY
    html = page(status)
    assert "ParishSoft data as of" in html and "last full refresh" in html
    assert html.count("data-local-instant") >= 2
    assert "UTC" not in html


def test_data_as_of_without_a_changed_quick_update_is_the_full_refresh(monkeypatch):
    """An empty quick update proves only the connection."""
    facts = SourceFacts(full_started_at=EARLY, success_at=LATE, answered_at=LATE)
    monkeypatch.setattr(refresh_status, "_facts", lambda row: facts)
    status = status_from(monkeypatch, (EARLY, None, False, None, LATE, None))
    assert status.data_as_of == EARLY and not status.shows_full_separately
    assert "last full refresh" not in page(status)


@pytest.mark.parametrize(
    "connection,text",
    [
        (Connection("failing", LATE), "failing since"),
        (Connection("not_checked", EARLY), "not checked since"),
        (Connection("working", LATE), "working (last answered"),
        (Connection("unknown"), "not checked yet"),
    ],
)
def test_connection_line_wording(connection, text):
    """The connection line is stated apart from "data as of"."""
    html = page(FullRefreshStatus(EARLY, None, False, connection=connection))
    assert "Connection:" in html and text in html


def test_late_full_refresh_is_named_with_how_late(monkeypatch):
    """Out-of-date data names the late scheduled full refresh and by how long."""
    facts = SourceFacts(full_started_at=EARLY)
    monkeypatch.setattr(refresh_status, "_facts", lambda row: facts)
    due = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    monkeypatch.setattr(refresh_status, "overdue_full_slot", lambda now, **_: due)
    status = status_from(monkeypatch, (EARLY, None, False, None), SCHEDULE, NOW)
    assert status.out_of_date and status.overdue_at == due
    assert status.late_minutes == 125
    html = page(status)
    assert "is 125 minutes late" in html and "notice-error" in html


def late(monkeypatch, *, running=False, held=False, sending=True, due=None):
    """The status for a full refresh due at ``due``, seen at 12:05 EDT (``NOW``).

    By default the refresh was due at 10:00 EDT; ``held`` says the send's
    allowance applies and ``sending`` whether the send is still running.
    """
    facts = SourceFacts(full_started_at=EARLY)
    monkeypatch.setattr(refresh_status, "_facts", lambda row: facts)
    due = due or datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    monkeypatch.setattr(refresh_status, "overdue_full_slot", lambda now, **_: due)
    monkeypatch.setattr(send_hold, "allowance_applies", lambda *args, **kw: held)
    monkeypatch.setattr(send_hold, "family_send_active", lambda: sending)
    return status_from(monkeypatch, (EARLY, None, running, None), SCHEDULE, NOW)


def test_the_run_button_sits_beside_the_late_notice_only(monkeypatch):
    """Past the margin and with nothing running, the notice carries the button.

    The settings page's idle button is not repeated below it, and the
    failure notice does not add a second one.
    """
    status = late(monkeypatch)
    assert status.offers_late_run
    html = page(status, can_refresh=True, refresh_key=KEY, idle_action=True)
    assert html.count(f'name="request_key" value="{KEY}"') == 1
    notice = html[html.index("is 125 minutes late") :]
    assert notice.index("Run a full refresh now") < notice.index("</div>")


def test_no_late_run_button_while_a_full_refresh_runs(monkeypatch):
    """A running full refresh is already the remedy: no second request."""
    status = late(monkeypatch, running=True)
    assert status.out_of_date and not status.offers_late_run
    html = page(status, can_refresh=True, refresh_key=KEY)
    assert "is 125 minutes late" in html
    assert "Run a full refresh now" not in html


def test_an_overdue_slot_within_the_margin_shows_no_notice(monkeypatch):
    """Due but within the margin (a refresh normally takes minutes): nothing."""
    facts = SourceFacts(full_started_at=EARLY)
    monkeypatch.setattr(refresh_status, "_facts", lambda row: facts)
    due = NOW.replace(minute=0)
    monkeypatch.setattr(refresh_status, "overdue_full_slot", lambda now, **_: due)
    status = status_from(monkeypatch, (EARLY, None, False, None), SCHEDULE, NOW)
    assert not status.out_of_date and status.overdue_at == due
    html = page(status, can_refresh=True, refresh_key=KEY)
    assert "late" not in html and "Run a full refresh now" not in html


def test_a_held_refresh_reads_held_not_late(monkeypatch):
    """Within a send's allowance the page says held, with the latest start."""
    # Due at 10:30 EDT, so the resume point (12:30 EDT) is still ahead.
    due = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)
    status = late(monkeypatch, held=True, due=due)
    assert status.held_for_send and status.resume_at > NOW
    assert not status.catching_up
    assert not status.offers_late_run
    html = page(status, can_refresh=True, refresh_key=KEY)
    assert "is held while Family emails are being sent" in html
    assert "minutes late" not in html and "notice-error" not in html


def test_no_data_yet_reads_like_the_digest():
    """The empty state matches the digest's wording."""
    html = page(FullRefreshStatus(None, None, False))
    assert "ParishSoft data: not yet loaded." in html
    assert "as of not yet" not in html


@pytest.mark.parametrize("case", ["send_ended", "past_resume_point"])
def test_a_held_refresh_after_the_send_or_resume_point_reads_catching_up(
    monkeypatch, case
):
    """Once the send has ended, or the resume point has passed, no "by" time.

    The allowance still applies to the catch-up, but "runs when the send
    ends, and by ... at the latest" would be wrong (the time may be past).
    """
    if case == "send_ended":
        status = late(monkeypatch, held=True, sending=False)
    else:
        # Due at 09:30 EDT: the resume point (12:00 EDT) is before NOW.
        due = datetime(2026, 9, 28, 13, 30, tzinfo=UTC)
        status = late(monkeypatch, held=True, sending=True, due=due)
        assert status.resume_at < NOW
    assert status.held_for_send and status.catching_up
    html = page(status, can_refresh=True, refresh_key=KEY)
    assert "is catching up after a Family send." in html
    assert "at the latest" not in html and "being sent" not in html
    assert "notice-error" not in html and "Run a full refresh now" not in html


def select_columns(sql):
    """Count the columns the statement's final top-level SELECT lists.

    Tracks parenthesis depth so commas inside sub-selects, function calls
    and the WITH clause's CTEs are not counted; the statements hold no
    commas or parentheses inside string literals.
    """
    depth, select, columns = 0, None, 0
    for match in re.finditer(r"[(),]|\bSELECT\b", sql):
        token = match.group()
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 0 and token == "SELECT":
            select, columns = match.start(), 1
        elif depth == 0 and token == "," and select is not None:
            columns += 1
    return columns


def test_the_status_row_halves_keep_their_column_counts():
    """The facts are read from the row's end, so both halves must agree (#659).

    ``full_refresh_status`` reads six outcome columns from the front and
    ``_facts`` the last ``FACTS_COLUMNS``; a column added to either statement
    without updating its reader fails here instead of showing wrong data.
    """
    assert select_columns(OUTCOMES_SQL) == 6
    assert select_columns(FACTS_SQL) == FACTS_COLUMNS
    assert len(fields(SourceFacts)) == FACTS_COLUMNS
    # System health's derived row (ADM-13), joined between the halves for
    # Home and read just before the facts.
    from parishkit.stewardship.system_health import (
        HEALTH_COLUMNS,
        HEALTH_SQL,
        HealthFacts,
    )

    assert select_columns(HEALTH_SQL) == HEALTH_COLUMNS
    assert len(fields(HealthFacts)) == HEALTH_COLUMNS


def test_facts_come_from_the_end_of_the_combined_row():
    """The data-age facts are the last columns, after the outcome half."""
    outcomes = (LATE, None, False, None, DELTA_OK, None)
    facts = (EARLY, DELTA_OK, LATE, LATE, True, EARLY, NOW)
    assert real_facts(outcomes + facts) == SourceFacts(*facts)


def test_listed_quick_times_are_called_quick_updates():
    """A schedule with listed quick times (#632) names them quick updates."""
    listed = FullRefreshStatus(
        EARLY, LATE, False, None, DELTA_OK, None, "daily", NOW, ("02:00",), "times"
    )
    assert listed.has_deltas and listed.deltas_healthy is True
    html = render_to_string(
        "stewardship/full-refresh-status.html", {"status": listed, "can_refresh": True}
    )
    assert "Last quick update:" in html and "quick updates are working" in html
    assert "15-minute" not in html and "hourly" not in html
