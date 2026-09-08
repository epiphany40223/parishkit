"""Sleep-free policy-clock scenarios across parish/browser and DST differences."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from parishkit.stewardship import clock
from parishkit.stewardship.testing import ManualClock


def test_system_clock_is_explicit_utc(monkeypatch):
    """A controlled datetime dependency proves the UTC request without wall sleeps."""
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    source = Mock()
    source.now.return_value = instant
    monkeypatch.setattr(clock, "datetime", source)
    assert clock.SystemClock().now() == instant
    source.now.assert_called_once_with(UTC)


@pytest.mark.parametrize("instant", [None, "2026-01-01", datetime(2026, 1, 1)])
def test_naive_or_non_datetime_is_not_authoritative(instant):
    """Machine-local timezone defaults cannot enter a scenario clock."""
    with pytest.raises(ValueError):
        ManualClock(instant)


@pytest.mark.parametrize("elapsed", [-1, "1 day", timedelta(microseconds=-1)])
def test_elapsed_time_cannot_reverse(elapsed):
    """Invalid advancement leaves the previously supplied instant unchanged."""
    instant = datetime(2026, 1, 1, tzinfo=UTC)
    timer = ManualClock(instant)
    with pytest.raises(ValueError):
        timer.advance(elapsed)
    assert timer.now() == instant


@pytest.mark.parametrize(
    ("instant", "expected_hour", "expected_fold"),
    [
        (datetime(2026, 3, 8, 6, 30, tzinfo=UTC), 3, 0),
        (datetime(2026, 11, 1, 5, 30, tzinfo=UTC), 1, 1),
    ],
)
def test_elapsed_utc_across_dst(instant, expected_hour, expected_fold):
    """One elapsed hour crosses a spring gap or autumn fold deterministically."""
    timer = ManualClock(instant.astimezone(ZoneInfo("America/New_York")))
    assert timer.now().tzinfo is UTC
    assert timer.advance(timedelta(0)) == instant
    result = timer.advance(timedelta(hours=1))
    local = clock.in_timezone(result, "America/New_York")
    assert local.hour == expected_hour and local.fold == expected_fold
    assert result - instant == timedelta(hours=1)


def test_parish_day_is_independent_of_browser_day():
    """The same UTC instant may have different local dates without clock mutation."""
    timer = ManualClock(datetime(2026, 1, 1, 2, tzinfo=UTC))
    instant = timer.now()
    parish = clock.in_timezone(instant, "America/New_York")
    browser = clock.in_timezone(instant, "Asia/Tokyo")
    assert parish.date().isoformat() == "2025-12-31"
    assert browser.date().isoformat() == "2026-01-01"
    assert parish.timestamp() == browser.timestamp()
    assert timer.now() == instant
    with pytest.raises(ZoneInfoNotFoundError):
        clock.in_timezone(instant, "not/a-timezone")
