"""Final preview freshness uses the same civil schedule rules as execution."""

from datetime import UTC, datetime, timedelta

import pytest

from parishkit.stewardship.campaigns.confirmation_window import confirmation_deadline
from parishkit.stewardship.campaigns.domain import UTCInterval

from .test_schedule_evaluation import plan

MINUTE = timedelta(minutes=1)
INTERVAL = UTCInterval(
    datetime(2026, 10, 1, 4, 1, tzinfo=UTC), datetime(2026, 11, 1, 4, tzinfo=UTC)
)


def deadline(observed, plans=(), *, source=None, interval=INTERVAL):
    """Keep independent source and campaign horizons visible in each scenario."""
    return confirmation_deadline(
        observed_at=observed,
        source_expires_at=source or observed + timedelta(hours=1),
        interval=interval,
        plans=plans,
    )


@pytest.mark.parametrize(
    "kind", ["initial", "reminder", "daily_digest", "weekly_digest"]
)
def test_next_due_slot_expires_preview_before_five_minutes(kind):
    """A physical message count may stay equal while its covered slots change."""
    day = 1 if kind in {"initial", "reminder"} else 2 if kind == "daily_digest" else 5
    due = datetime(2026, 10, day, 13, tzinfo=UTC)
    rule = plan(kind)
    assert deadline(due - 2 * MINUTE, [rule]) == due
    assert deadline(due, [rule]) == due + 5 * MINUTE


@pytest.mark.parametrize("boundary", ["source", "start", "close", "dns"])
def test_nearest_non_schedule_boundary_expires_preview(boundary):
    """Crossing the start changes scheduled/active even without any due mail."""
    observed = datetime(2026, 10, 10, 12, tzinfo=UTC)
    source, interval = observed + 60 * MINUTE, INTERVAL
    if boundary == "source":
        source = observed + MINUTE
    elif boundary == "start":
        interval = UTCInterval(observed + MINUTE, INTERVAL.end)
    elif boundary == "close":
        interval = UTCInterval(INTERVAL.start, observed + MINUTE)
    assert (
        deadline(observed, source=source, interval=interval)
        == observed + (5 if boundary == "dns" else 1) * MINUTE
    )


def test_long_due_history_does_not_hide_the_next_page_boundary():
    """A preview after more than 100 historical dates still finds the next slot."""
    rule = plan(campaign_options={"start_date": "2026-01-01"})
    due = datetime(2026, 10, 10, 13, tzinfo=UTC)
    assert deadline(due - MINUTE, [rule]) == due


def test_fold_uses_the_existing_earlier_instant():
    """Do not silently keep a preview until the second wall-clock occurrence."""
    rule = plan(
        campaign_options={"start_date": "2026-10-30", "end_date": "2026-11-03"},
        time="01:30:00",
    )
    due = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    interval = UTCInterval(INTERVAL.start, due + timedelta(days=2))
    assert deadline(due - MINUTE, [rule], interval=interval) == due


@pytest.mark.parametrize("source", [0, -1])
def test_expired_source_cannot_create_a_confirmation_window(source):
    observed = datetime(2026, 10, 10, 12, tzinfo=UTC)
    with pytest.raises(ValueError, match="already expired"):
        deadline(observed, source=observed + source * MINUTE)
