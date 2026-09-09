"""Campaign boundaries are shared by admission, schedules and reporting buckets."""

from datetime import UTC, date, datetime, timedelta

import pytest

from parishkit.stewardship.campaigns.intervals import (
    campaign_interval,
    financial_period_end,
    local_day,
    resolve_local,
)


@pytest.mark.parametrize(
    "day,hours",
    [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25), (date(2026, 6, 1), 24)],
)
def test_dst_day_lengths(day, hours):
    """Day duration follows the campaign zone rather than fixed elapsed days."""
    interval = local_day(day, "America/New_York").interval
    assert interval.end - interval.start == timedelta(hours=hours)
    assert interval.contains(interval.start)
    assert not interval.contains(interval.end)


def test_gap_moves_to_first_valid_instant_and_fold_uses_earlier_occurrence():
    """A nonexistent 02:30 is advanced to 03:00, not to 03:30."""
    assert resolve_local(datetime(2026, 3, 8, 2, 30), "America/New_York") == datetime(
        2026, 3, 8, 7, tzinfo=UTC
    )
    assert resolve_local(datetime(2026, 11, 1, 1, 30), "America/New_York") == datetime(
        2026, 11, 1, 5, 30, tzinfo=UTC
    )


def test_whole_skipped_day_is_empty_and_adjacent_days_join():
    """The Samoa date-line change must not fabricate an extra reporting day."""
    days = [
        local_day(date(2011, 12, day), "Pacific/Apia").interval for day in (29, 30, 31)
    ]
    assert days[0].end == days[1].start == days[1].end == days[2].start


def test_campaign_includes_whole_last_day():
    """The canonical close is the next midnight, never 23:59 or 00:01."""
    interval = campaign_interval(date(2026, 3, 7), date(2026, 3, 9), "America/New_York")
    assert interval.start == datetime(2026, 3, 7, 5, tzinfo=UTC)
    assert interval.end == datetime(2026, 3, 10, 4, tzinfo=UTC)


@pytest.mark.parametrize("zone", [None, "", "not/a/zone", "/etc/passwd"])
def test_invalid_zones_fail_safely(zone):
    """Invalid identifiers are not echoed into errors."""
    with pytest.raises(ValueError):
        resolve_local(datetime(2026, 1, 1), zone)


@pytest.mark.parametrize(
    "value", [None, date(2026, 1, 1), datetime(2026, 1, 1, tzinfo=UTC)]
)
def test_local_resolution_rejects_wrong_input(value):
    """The caller must explicitly distinguish wall times and instants."""
    with pytest.raises(ValueError):
        resolve_local(value, "UTC")


@pytest.mark.parametrize("day", [None, datetime(2026, 1, 1), date.max])
def test_day_rejects_invalid_or_unrepresentable_dates(day):
    """A closing boundary must be representable."""
    with pytest.raises(ValueError):
        local_day(day, "UTC")


@pytest.mark.parametrize("end", [None, date(2025, 1, 1), date(2026, 1, 1)])
def test_campaign_date_order(end):
    """A campaign must span strictly increasing configured dates."""
    with pytest.raises(ValueError):
        campaign_interval(date(2026, 1, 1), end, "UTC")


@pytest.mark.parametrize(
    "start,end",
    [
        (date(2026, 1, 1), date(2026, 12, 31)),
        (date(2025, 7, 1), date(2026, 6, 30)),
        (date(2024, 2, 29), date(2025, 2, 27)),
    ],
)
def test_exact_financial_anniversary(start, end):
    """Calendar anniversaries, not 365-day arithmetic, define the period."""
    assert financial_period_end(start) == end


@pytest.mark.parametrize("value", [None, date.max, datetime(2026, 1, 1)])
def test_financial_period_rejects_invalid_dates(value):
    """Reject impossible anniversary calculations before configuration persistence."""
    with pytest.raises(ValueError):
        financial_period_end(value)
