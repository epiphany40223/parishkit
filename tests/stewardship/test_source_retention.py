"""UTC retention boundaries and deterministic anchor selection without storage."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from parishkit.stewardship.source.retention_policy import (
    retention_anchors,
    retention_cutoffs,
)

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)


def stamps(*instants):
    """Give synthetic promotions independent identities and ordered generations."""
    return [(uuid4(), instant, index + 1) for index, instant in enumerate(instants)]


def test_retains_all_recent_and_latest_utc_daily_then_monthly_anchors():
    """Array order never changes retained historical source truth."""
    rows = stamps(
        NOW - timedelta(days=1),
        NOW - timedelta(days=1, hours=1),
        datetime(2026, 1, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 1, 23, tzinfo=UTC),
        datetime(2026, 1, 2, 1, tzinfo=UTC),
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 31, 23, tzinfo=UTC),
        datetime(2024, 2, 1, tzinfo=UTC),
    )
    expected = {rows[index][0] for index in (0, 1, 3, 4, 6, 7)}
    assert retention_anchors(rows, now=NOW) == expected
    assert retention_anchors(reversed(rows), now=NOW) == expected


def test_exact_recent_cutoff_and_generation_tie_breaking():
    """The exact 90-day instant is retained; older daily ties use generation."""
    cutoff = NOW - timedelta(days=90)
    rows = stamps(
        cutoff, cutoff, cutoff - timedelta(days=1), cutoff - timedelta(days=1)
    )
    assert retention_anchors(rows, now=NOW) == {rows[index][0] for index in (0, 1, 3)}


def test_calendar_year_cutoff_and_leap_day():
    """One year is a calendar boundary, not an assumed fixed 365-day duration."""
    instant = datetime(2024, 2, 29, 12, tzinfo=UTC)
    recent, yearly = retention_cutoffs(instant)
    assert recent == instant - timedelta(days=90)
    assert yearly == datetime(2023, 2, 28, 12, tzinfo=UTC)
    boundary = NOW.replace(year=NOW.year - 1)
    rows = stamps(
        boundary,
        boundary + timedelta(days=1),
        boundary - timedelta(days=1),
        boundary - timedelta(days=2),
    )
    assert retention_anchors(rows, now=NOW) == {rows[index][0] for index in (0, 1, 2)}


def test_daily_grouping_uses_utc_not_input_offset():
    """Two local dates can belong to one UTC anchor day."""
    eastern = timezone(timedelta(hours=-4))
    rows = stamps(
        datetime(2026, 1, 1, 23, tzinfo=eastern), datetime(2026, 1, 2, 5, tzinfo=UTC)
    )
    assert retention_anchors(rows, now=NOW) == {rows[1][0]}


@pytest.mark.parametrize("now", [None, "2026-01-01", datetime(2026, 1, 1)])
def test_cutoffs_require_aware_instants(now):
    """Never infer the parish/browser timezone for a storage-retention cutoff."""
    with pytest.raises(ValueError, match="aware"):
        retention_cutoffs(now)


@pytest.mark.parametrize(
    "row",
    [
        ("bad", NOW, 1),
        (uuid4(), NOW, True),
        (uuid4(), NOW, 0),
        (uuid4(), datetime(2026, 1, 1), 1),
    ],
)
def test_anchor_metadata_must_be_valid(row):
    """Malformed persisted input cannot silently select a destructive target set."""
    with pytest.raises(ValueError, match="metadata"):
        retention_anchors([row], now=NOW)
