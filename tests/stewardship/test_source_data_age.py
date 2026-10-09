"""Data age, the out-of-date rule and the connection line, without a database.

The worked examples follow the operations spec ("ParishSoft data age and
connection") and the refresh schedule plan: full refreshes at 00:00 and every
two hours from 08:00 to 20:00 parish time (America/New_York, EDT in October),
with the default 30-minute lateness margin.
"""

from datetime import UTC, datetime, timedelta

import pytest

from parishkit.stewardship.reports.daily_digest import source_age_line
from parishkit.stewardship.source.cadence import refresh_settings
from parishkit.stewardship.source.data_age import (
    Connection,
    DataAge,
    SourceFacts,
    changed,
    connection_state,
    connection_threshold,
    first_overdue,
    full_due_after,
    is_out_of_date,
)

ZONE = "America/New_York"
MARGIN = timedelta(minutes=30)
# A full refresh starts a few seconds after its due time.
READ = timedelta(seconds=5)
PRODUCTION = refresh_settings(
    {
        "nightly_time": "00:00",
        "full_refresh_times": [
            "00:00",
            "08:00",
            "10:00",
            "12:00",
            "14:00",
            "16:00",
            "18:00",
            "20:00",
        ],
        "delta_refresh": "quarter_hour",
    }
)
NIGHTLY = refresh_settings({"nightly_time": "02:00", "delta_refresh": "hourly"})


def local(hour, minute=0, day=6):
    """An instant on October ``day``, 2026 at ``hour:minute`` EDT (UTC-4)."""
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC) + timedelta(hours=4)


def test_full_due_after_finds_the_next_listed_local_time():
    """The next full time after a read, in the parish's zone."""
    assert full_due_after(PRODUCTION, ZONE, local(8, 1), local(23)) == local(10)
    assert full_due_after(PRODUCTION, ZONE, local(20), local(23)) is None
    # Across midnight to the next day's 00:00.
    assert full_due_after(PRODUCTION, ZONE, local(20), local(1, day=7)) == local(
        0, day=7
    )
    # The bound is inclusive at its end, exclusive at its start.
    assert full_due_after(PRODUCTION, ZONE, local(9), local(10)) == local(10)
    assert full_due_after(PRODUCTION, ZONE, local(10), local(11)) is None


def test_full_due_after_uses_the_daylight_saving_resolver():
    """On the day clocks go back, 02:00 runs once, at its first instant (EDT)."""
    back = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)  # 01:00 EDT
    assert full_due_after(NIGHTLY, ZONE, back, back + timedelta(hours=4)) == (
        datetime(2026, 11, 1, 7, 0, tzinfo=UTC)  # 02:00 EST
    )


@pytest.mark.parametrize(
    "frequency,expected",
    [("hourly", local(11)), ("quarter_hour", local(10, 15))],
)
def test_legacy_frequent_full_refreshes_fall_on_utc_boundaries(frequency, expected):
    """An existing hourly or quarter-hour full refresh keeps its UTC slots."""
    settings = refresh_settings({"full_refresh": frequency})
    assert full_due_after(settings, ZONE, local(10, 7), local(23)) == expected


def test_nightly_only_data_is_out_of_date_at_0230():
    """With the nightly refresh alone, a missed 02:00 alarms at 02:30."""
    runs = [(local(0, day=1), NIGHTLY)]
    after = local(2, 1, day=5)
    overdue = first_overdue(runs, ZONE, after, local(2, 29))
    assert overdue == local(2)
    assert not is_out_of_date(overdue, local(2, 29), MARGIN)
    assert is_out_of_date(overdue, local(2, 30), MARGIN)


def test_a_missed_daytime_refresh_is_reported_at_1030():
    """A quick update every 15 minutes no longer hides a missed 10:00 refresh."""
    runs = [(local(0, day=1), PRODUCTION)]
    overdue = first_overdue(runs, ZONE, local(8) + READ, local(10, 31))
    assert overdue == local(10)
    assert is_out_of_date(overdue, local(10, 31), MARGIN)


def test_saving_a_schedule_never_alarms_over_times_not_yet_scheduled():
    """Due times of the new schedule before it took effect do not count."""
    runs = [(local(0, day=1), NIGHTLY), (local(10, 5), PRODUCTION)]
    # The full refresh read at 02:00; the new schedule's 08:00 and 10:00 were
    # not scheduled then, so the overdue slot is its 12:00.
    assert first_overdue(runs, ZONE, local(2) + READ, local(12, 40)) == local(12)
    assert first_overdue(runs, ZONE, local(2) + READ, local(11)) is None


def test_a_schedule_change_while_a_full_slot_is_overdue_keeps_it_overdue():
    """The spec's 10:05 example: the old 10:00 slot stays the overdue slot.

    The Administrator switches to the nightly-only default at 10:05 while
    the 10:00 full refresh has not run; the alarm still sounds at 10:30.
    """
    runs = [(local(0, day=1), PRODUCTION), (local(10, 5), NIGHTLY)]
    overdue = first_overdue(runs, ZONE, local(8) + READ, local(10, 31))
    assert overdue == local(10)
    assert is_out_of_date(overdue, local(10, 31), MARGIN)


def test_a_configuration_without_parishsoft_schedules_nothing():
    """A run with no schedule has no due times."""
    assert first_overdue([(local(0, day=1), None)], ZONE, local(1), local(23)) is None


@pytest.mark.parametrize(
    "cursor,expected",
    [
        ({"changes": {"family": 1, "member": 0}}, True),
        ({"changes": {"family": 0, "member": 0}}, False),
        ({"changes": {}}, False),
        ({}, False),
        ({"changes": None}, False),
        ({"changes": {"family": True}}, False),
        (None, False),
    ],
)
def test_only_a_counted_change_moves_data_as_of(cursor, expected):
    """A quick update without counts, or with zero counts, changes nothing."""
    assert changed(cursor) is expected


def test_data_as_of_is_the_newer_of_full_and_changed_quick():
    """An empty quick update does not move "data as of"; a changed one does."""
    assert SourceFacts(full_started_at=local(8)).data_as_of == local(8)
    assert SourceFacts(
        full_started_at=local(8), changed_delta_at=local(11)
    ).data_as_of == local(11)
    assert SourceFacts().data_as_of is None


def connection(facts, now, *, sending=False, held=None):
    """The line for ``facts`` at ``now`` with a one-hour threshold."""
    return connection_state(
        facts,
        now=now,
        threshold=timedelta(hours=1),
        sending=lambda: sending,
        held=lambda: held,
    )


def test_connection_precedence_failing_then_not_checked_then_working():
    """Failing since the first failure after the newest success comes first."""
    failing = SourceFacts(
        success_at=local(9), newest_failed=True, failing_since=local(9, 15)
    )
    assert connection(failing, local(9, 20)) == Connection("failing", local(9, 15))
    stale = SourceFacts(success_at=local(2), answered_at=local(2, 3))
    assert connection(stale, local(4)) == Connection("not_checked", local(2))
    fresh = SourceFacts(success_at=local(9), answered_at=local(9, 2))
    assert connection(fresh, local(9, 30)) == Connection("working", local(9, 2))
    assert connection(SourceFacts(), local(9)) == Connection("unknown")


def test_held_time_does_not_count_toward_not_checked():
    """A send alone never makes the line read "not checked"."""
    stale = SourceFacts(success_at=local(2), answered_at=local(2, 3))
    assert connection(stale, local(4), sending=True).state == "working"
    # The scheduler's newest durable hold entry restarts the gap.
    held = SourceFacts(success_at=local(2), answered_at=local(2, 3), held_at=local(3))
    assert connection(held, local(3, 30)).state == "working"


def test_a_go_live_hold_end_restarts_the_gap_and_is_read_lazily():
    """The latest go-live hold end counts like a send hold (#462).

    It is read only when the gap from the last success is exceeded.
    """
    stale = SourceFacts(success_at=local(2), answered_at=local(2, 3))
    # Within the threshold after the hold ended: still working.
    assert connection(stale, local(4), held=local(3, 30)).state == "working"
    # More than the threshold after it: not checked again.
    assert connection(stale, local(5), held=local(3, 30)).state == "not_checked"
    # A fresh line never asks for the hold end.
    fresh = SourceFacts(success_at=local(9), answered_at=local(9, 2))

    def forbidden():
        """The hold end must not be read while the gap is not exceeded."""
        raise AssertionError("read the go-live hold end")

    assert (
        connection_state(
            fresh, now=local(9, 30), threshold=timedelta(hours=1), held=forbidden
        ).state
        == "working"
    )


def test_connection_threshold_is_the_longest_gap_plus_the_margin():
    """With quick updates off, the gap is the longest wait between full times."""
    off = refresh_settings({"nightly_time": "02:00", "delta_refresh": "off"})
    assert connection_threshold(off, MARGIN) == timedelta(hours=24, minutes=30)
    assert connection_threshold(NIGHTLY, MARGIN) == timedelta(hours=1, minutes=30)
    # Listed quick times (#632) count toward the gap.
    listed = refresh_settings(
        {
            "nightly_time": "02:00",
            "delta_refresh": "times",
            "quick_refresh_times": ["08:00", "14:00", "20:00"],
        }
    )
    assert connection_threshold(listed, MARGIN) == timedelta(hours=6, minutes=30)


def test_digest_line_states_data_age_and_connection_in_the_parish_zone():
    """One line, every time in the parish's zone in one format."""
    age = DataAge(local(11), local(8), Connection("working", local(11, 15)))
    line = source_age_line(age, ZONE)
    assert line.startswith("ParishSoft data as of ")
    assert ", last full refresh " in line
    assert "Connection: working (last answered " in line
    assert "UTC" not in line and "EDT" in line
    same = DataAge(local(8), local(8), Connection("failing", local(9)))
    line = source_age_line(same, ZONE)
    assert "last full refresh" not in line and "failing since" in line
    assert source_age_line(DataAge(None, None, Connection("unknown")), ZONE) == (
        "ParishSoft data: not yet loaded. Connection: not checked yet."
    )


def test_data_age_refuses_naive_instants():
    """The digest document accepts only aware instants."""
    with pytest.raises(ValueError):
        DataAge(datetime(2026, 10, 6), None, Connection("unknown"))
    with pytest.raises(ValueError):
        DataAge(None, None, "working")
