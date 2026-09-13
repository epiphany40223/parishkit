"""Exact wizard session, source-watchdog and bounded-renewal boundaries."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.setup_policy import (
    ExpiryReason,
    SetupState,
    SetupWindow,
)

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)


def window(**changes):
    """A synthetic authenticated session has twelve hours and one bound source Task."""
    values = dict(
        session_id=uuid4(),
        state=SetupState.LOADING,
        activity_at=NOW,
        absolute_at=NOW + timedelta(hours=12),
        task_id=uuid4(),
        task_created_at=NOW,
    )
    return SetupWindow(**(values | changes))


def test_visible_deadlines_are_independent_and_never_extended_by_progress():
    """The two-hour watchdog stays fixed while bounded polling keeps idle live."""
    observed = window(
        activity_at=NOW + timedelta(minutes=110),
        renewed_at=NOW + timedelta(minutes=110),
    )
    assert observed.idle_at == NOW + timedelta(minutes=140)
    assert observed.watchdog_at == NOW + timedelta(hours=2)
    assert observed.absolute_at == NOW + timedelta(hours=12)
    assert (
        observed.expiry(NOW + timedelta(hours=2), session_live=True)
        is ExpiryReason.WATCHDOG
    )


@pytest.mark.parametrize(
    "elapsed,allowed",
    [(0, False), (299, False), (300, True), (1799, True), (1800, False)],
)
def test_first_renewal_is_throttled_and_cannot_revive_idle_session(elapsed, allowed):
    """Renewal starts at five minutes and cannot revive thirty-minute idle expiry."""
    observed = window()
    assert (
        observed.may_renew(
            NOW + timedelta(seconds=elapsed),
            session_id=observed.session_id,
            task_id=observed.task_id,
            worker_live=True,
            session_live=True,
        )
        is allowed
    )


def test_repeat_renewals_are_throttled_from_last_accepted_request():
    """Frequent polls cannot increment activity continuously or move the watchdog."""
    observed = window(
        activity_at=NOW + timedelta(minutes=5), renewed_at=NOW + timedelta(minutes=5)
    )
    for minute, allowed in ((5, False), (9, False), (10, True)):
        assert (
            observed.may_renew(
                NOW + timedelta(minutes=minute),
                session_id=observed.session_id,
                task_id=observed.task_id,
                worker_live=True,
                session_live=True,
            )
            is allowed
        )


@pytest.mark.parametrize(
    "reason", ["session", "task", "worker", "revoked", "terminal", "frozen"]
)
def test_unrelated_or_nonlive_progress_never_renews(reason):
    """Task/session UUID knowledge cannot turn another tab or task into a keepalive."""
    observed = window()
    if reason in {"terminal", "frozen"}:
        observed = replace(
            observed,
            state=SetupState.COLLECTING if reason == "terminal" else SetupState.FROZEN,
        )
    assert not observed.may_renew(
        NOW + timedelta(minutes=5),
        session_id=uuid4() if reason == "session" else observed.session_id,
        task_id=uuid4() if reason == "task" else observed.task_id,
        worker_live=reason != "worker",
        session_live=reason != "revoked",
    )


@pytest.mark.parametrize(
    "state", [SetupState.COLLECTING, SetupState.LOADING, SetupState.FROZEN]
)
def test_absolute_expiry_and_session_revocation_apply_to_every_pending_phase(state):
    """Installer checkpoints never make a frozen wizard immortal or resumable."""
    observed = window(state=state, activity_at=NOW + timedelta(hours=11, minutes=59))
    assert observed.idle_at == observed.absolute_at
    assert (
        observed.expiry(observed.absolute_at, session_live=True)
        is ExpiryReason.ABSOLUTE
    )
    assert observed.expiry(NOW, session_live=False) is ExpiryReason.SESSION


def test_source_watchdog_does_not_expire_a_completed_load_during_later_data_entry():
    """The watchdog bounds loading; session expiry still bounds later steps."""
    observed = window(state=SetupState.COLLECTING, activity_at=NOW + timedelta(hours=3))
    assert (
        observed.expiry(NOW + timedelta(hours=3, minutes=1), session_live=True) is None
    )
    assert (
        replace(observed, state=SetupState.COMPLETED).expiry(
            NOW + timedelta(days=1), session_live=False
        )
        is None
    )
    assert (
        replace(observed, state=SetupState.EXPIRED).expiry(NOW, session_live=True)
        is ExpiryReason.SESSION
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"session_id": "not-a-session"},
        {"state": "loading"},
        {"activity_at": NOW.replace(tzinfo=None)},
        {"absolute_at": NOW},
        {"task_id": None},
        {"task_created_at": None},
        {"task_id": "not-a-task"},
        {"task_created_at": NOW + timedelta(hours=12)},
        {"renewed_at": NOW - timedelta(seconds=1)},
        {"renewed_at": NOW + timedelta(seconds=1)},
    ],
)
def test_invalid_persisted_observations_fail_closed(changes):
    """Corrupt or partial bindings cannot be treated as live setup authority."""
    with pytest.raises(ValueError):
        window(**changes)


def test_boolean_liveness_cannot_be_coerced_from_browser_values():
    """The database-owning caller must pass actual decisions, not truthy strings."""
    observed = window()
    with pytest.raises(TypeError):
        observed.expiry(NOW, session_live="yes")
    with pytest.raises(TypeError):
        observed.may_renew(
            NOW,
            session_id=observed.session_id,
            task_id=observed.task_id,
            worker_live="yes",
            session_live=True,
        )
