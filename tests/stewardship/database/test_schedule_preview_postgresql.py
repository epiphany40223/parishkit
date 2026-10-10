"""The refresh schedule preview against the real scheduler and records (#632).

The real scheduler runs one loop at every instant the preview says a slot
falls due, across an ordinary day and both daylight-saving days in the
parish's zone (America/New_York), and creates exactly the ticks the preview
says run. The preview's readers find the Family email windows the scheduler
will apply over the coming week, measure recent successful runs, and work
under the web login the settings page uses.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from django.db import transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.source import production, send_windows
from parishkit.stewardship.source.cadence import listed_due_slots
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.production import produce_refreshes
from parishkit.stewardship.source.refresh_models import SourceRefreshTick
from parishkit.stewardship.source.refresh_rules import stored_settings
from parishkit.stewardship.source.schedule_preview import (
    MINIMUM_RUNS,
    TYPICAL,
    measured_durations,
    preview_days,
    schedule_preview,
)

from ..test_schedule_preview import BUSY, NIGHTLY_IN_GAP, ZONE, expected_runs, rules
from .test_background_grants_postgresql import task_login
from .test_family_mail_prepare_ahead_postgresql import (  # noqa: F401
    dispatch_worker,
    families,
    family_mail,
    invited,
)
from .test_refresh_frequency_postgresql import with_schedule
from .test_source_attempts_postgresql import configured
from .test_source_health_postgresql import publish

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Restore idle source seeds after the disposable database flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def produce(monkeypatch, instant):
    """Run one scheduler loop as if the database clock read ``instant``."""
    monkeypatch.setattr(production, "database_now", lambda: instant)
    with scheduler_session() as guard:
        return produce_refreshes(guard)


@pytest.mark.parametrize(
    "day",
    [
        date(2026, 3, 8),  # clocks go forward: 02:00 EST becomes 03:00 EDT
        date(2025, 11, 2),  # clocks go back: 02:00 EDT becomes 01:00 EST
        date(2026, 3, 10),  # an ordinary day
    ],
)
@pytest.mark.parametrize("rows", [BUSY, NIGHTLY_IN_GAP])
def test_the_real_scheduler_creates_the_ticks_the_preview_says_run(
    tmp_path, monkeypatch, day, rows
):
    """One loop at each due instant; the day's ticks are the preview's runs.

    The instants come from the scheduler's own ``listed_due_slots`` (every
    listed slot due by the end of the day), not from the preview, so a slot
    the preview left out still gets a loop at its instant and shows up as a
    tick the preview does not expect.
    """
    document = rules(*rows)
    settings = stored_settings(document)
    with_schedule(tmp_path, **settings)
    (preview,) = preview_days(document, timezone=ZONE, first=day, days=1)
    slots = listed_due_slots(
        now=preview.end - timedelta(microseconds=1),
        timezone=ZONE,
        nightly_time=settings["nightly_time"],
        scope_fingerprint="0" * 64,
        full_refresh_times=settings["full_refresh_times"],
        quick_refresh_times=settings.get("quick_refresh_times", ()),
    )
    instants = sorted(
        {s.due_at for s in slots if preview.start <= s.due_at < preview.end}
    )
    for instant in instants:
        produce(monkeypatch, instant)
    ticks = {
        (
            tick.command.cause,
            tick.due_at,
            tick.nightly_time if tick.command.cause == "nightly" else None,
        )
        for tick in SourceRefreshTick.objects.select_related("command")
        if preview.start <= tick.due_at < preview.end
    }
    assert ticks == expected_runs(preview)


def test_the_window_reader_looks_ahead_for_the_preview(invited):  # noqa: F811
    """Three days ahead, the reminder is beyond the scheduler's horizon only."""
    harness, path, provider, due_at = invited
    now = due_at - timedelta(days=3)
    with task_login(ServiceRole.WEB), transaction.atomic():
        current = send_windows.current_windows(now)
        upcoming = send_windows.upcoming_windows(now, due_at)
    assert not [w for w in current if w.cause.startswith("reminder")]
    reminder = [w for w in upcoming if w.cause.startswith("reminder")]
    assert [w.cause for w in reminder] == ["reminder_preparing", "reminder_sending"]
    assert reminder[0].start == due_at - timedelta(hours=2)
    # The same windows the scheduler reads once the reminder is near.
    with transaction.atomic():
        near = send_windows.current_windows(due_at - timedelta(hours=1))
    assert [w for w in near if w.cause.startswith("reminder")] == reminder


def test_durations_are_the_medians_of_recent_successful_runs(tmp_path):
    """Three quick updates are measured; one full refresh is not yet enough."""
    credential, *_ = configured(tmp_path)
    publish(credential)
    quick = [publish(credential, kind="delta") for _ in range(MINIMUM_RUNS)]
    now = datetime.now(UTC) + timedelta(minutes=1)
    with task_login(ServiceRole.WEB), transaction.atomic():
        found = measured_durations(now)
    lengths = sorted(snapshot.completed_at - snapshot.started_at for snapshot in quick)
    assert (found.quick, found.quick_measured) == (lengths[1], True)
    assert (found.full, found.full_measured) == (TYPICAL["full"], False)
    # Runs older than the last seven days are not measured.
    with transaction.atomic():
        assert not measured_durations(now + timedelta(days=8)).quick_measured


def test_the_settings_page_login_reads_the_whole_preview(tmp_path):
    """The web login reads the measured runs, windows and margin it needs."""
    credential, *_ = configured(tmp_path)
    publish(credential)
    document = rules(*BUSY, skip=True)
    now = datetime(2026, 9, 11, 17, 23, tzinfo=UTC)
    with task_login(ServiceRole.WEB), transaction.atomic():
        found = schedule_preview(document, now=now, timezone=ZONE)
    assert [day.day for day in found.days] == [
        date(2026, 9, 11) + timedelta(days=offset) for offset in range(7)
    ]
    # Testing mode: the scheduler applies no windows, so neither does the preview.
    assert found.windows == ()
    assert found.freshness.margin > timedelta()
    assert found.cost.durations.full == TYPICAL["full"]
