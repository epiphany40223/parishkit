"""The shared schedule wall-clock primitives (#1129)."""

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from parishkit.stewardship import wall_clock
from parishkit.stewardship.wall_clock import (
    WEEKDAYS,
    hhmm,
    is_hhmm,
    wall_hhmm,
    weekday_choices,
)

SOURCE = Path(wall_clock.__file__).resolve().parent


def test_weekdays_are_monday_first_like_date_weekday():
    """Index ``d.weekday()`` names the date's own day, Monday first."""
    monday = date(2026, 10, 12)
    for offset in range(7):
        day = monday + timedelta(days=offset)
        assert str(WEEKDAYS[day.weekday()]) == day.strftime("%A")


def test_weekday_choices_pair_each_stored_index_with_its_name():
    """A day picker's choices are the stored index and the day's name."""
    choices = weekday_choices()
    assert [index for index, _ in choices] == list(range(7))
    assert [str(name) for _, name in choices][:2] == ["Monday", "Tuesday"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("00:00", True),
        ("02:00", True),
        ("23:59", True),
        ("2:00", False),
        ("24:00", False),
        ("12:60", False),
        ("02:00:00", False),
        (" 02:00", False),
        ("", False),
        (None, False),
        (time(2, 0), False),
    ],
)
def test_is_hhmm_accepts_only_canonical_wall_times(value, expected):
    """Only two-digit 24-hour ``HH:MM`` text is a stored wall time."""
    assert is_hhmm(value) is expected


def test_hhmm_formats_times_and_datetimes_and_keeps_none():
    """``hhmm`` drops seconds and passes None through."""
    assert hhmm(time(7, 5, 30)) == "07:05"
    assert hhmm(datetime(2026, 10, 14, 21, 45)) == "21:45"
    assert hhmm(None) is None


def test_wall_hhmm_reads_the_instant_in_the_named_zone():
    """An instant reads as the zone's wall time, daylight saving included."""
    summer = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)
    winter = datetime(2026, 12, 1, 7, 0, tzinfo=UTC)
    assert wall_hhmm(summer, "America/New_York") == "02:00"
    assert wall_hhmm(winter, "America/New_York") == "02:00"
    assert wall_hhmm(winter, "UTC") == "07:00"


def test_no_feature_keeps_its_own_weekday_table():
    """The weekday names are defined only here, never per feature again."""
    owners = [
        path.relative_to(SOURCE).as_posix()
        for path in SOURCE.rglob("*.py")
        if path.name != "wall_clock.py"
        and '"Monday"' in path.read_text(encoding="utf-8")
    ]
    assert owners == []
