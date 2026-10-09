"""An export that keeps overrunning its read guard stops being retried (#386, L3).

The count comes from ``stewardship_read_guard_kills_v1`` (exercised against
PostgreSQL in ``database/test_export_guard_stops_postgresql.py``); here it is
a stand-in, and the recovery rule around it is the real code.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.reports import export_tasks


@pytest.fixture
def abandoned(monkeypatch):
    """An abandoned export with no outcome; returns (status, set stops)."""
    stops = {"count": 0}
    monkeypatch.setattr(
        export_tasks,
        "bound_request",
        lambda status: SimpleNamespace(campaign_id=uuid4()),
    )
    monkeypatch.setattr(export_tasks, "_outcome", lambda request: None)
    monkeypatch.setattr(export_tasks, "admit_campaign", lambda *a, **k: None)
    monkeypatch.setattr(
        export_tasks,
        "guard_stops",
        lambda run_id: stops.__setitem__("seen", run_id) or stops["count"],
    )

    def status(attempt):
        return SimpleNamespace(state="abandoned", attempt=attempt, run_id=uuid4())

    return status, stops


@pytest.mark.parametrize(
    "count,action", [(0, "recovery_retry"), (1, "recovery_retry"), (2, "recovery_fail")]
)
def test_a_second_guard_stop_ends_the_retries(abandoned, count, action):
    """One stop is retried (a slow moment); a second means it always overruns."""
    status, stops = abandoned
    stops["count"] = count
    current = status(2)
    plan = export_tasks.recover_export(current)
    assert plan.action == action
    assert stops["seen"] == current.run_id
    if count == 1:
        # One slow spell of up to ten minutes passes before the one retry.
        assert plan.retry_seconds == export_tasks.MAX_RETRY_SECONDS
    elif count == 0:
        assert plan.retry_seconds == 60  # attempt 2's ordinary backoff


def test_the_attempt_limit_still_applies_without_guard_stops(abandoned):
    """Five attempts fail as before, whatever the count."""
    status, _ = abandoned
    assert export_tasks.recover_export(status(5)).action == "recovery_fail"
    assert export_tasks.recover_export(status(4)).action == "recovery_retry"
