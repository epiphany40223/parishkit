"""Full-refresh status: a later success clears the failure banner and its link."""

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from django.template.loader import render_to_string

from parishkit.stewardship.source import refresh_status
from parishkit.stewardship.source.refresh_status import FullRefreshStatus

EARLY = datetime(2026, 9, 28, 6, 3, tzinfo=UTC)
LATE = datetime(2026, 9, 28, 11, 15, tzinfo=UTC)


def status_from(monkeypatch, row):
    """Run full_refresh_status against one canned database row."""

    class Cursor:
        def execute(self, sql):
            self.sql = sql

        def fetchone(self):
            return row

    class Connection:
        @contextmanager
        def cursor(self):
            yield Cursor()

    monkeypatch.setattr(refresh_status, "connection", Connection())
    return refresh_status.full_refresh_status()


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
    assert f"/admin/background/task/{task}" in html
    assert "See Background work" not in html


def test_banner_omits_the_link_for_users_who_cannot_open_tasks():
    """Staff see the failure notice but no link to a page they cannot open."""
    task = uuid4()
    html = render_to_string(
        "stewardship/full-refresh-status.html",
        {"status": FullRefreshStatus(EARLY, LATE, False, task), "can_view_task": False},
    )
    assert "notice-error" in html
    assert f"/admin/background/task/{task}" not in html


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
    assert 'method="post" action="/admin/source/refresh"' in html
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
        def execute(self, sql):
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
