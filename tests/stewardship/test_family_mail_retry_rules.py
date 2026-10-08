"""Family mail retry rules from the #298 review follow-ups (#382)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    ProviderHealth,
    sending_limit,
)
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs import family_mail_dispatch as dispatch

NOW = datetime(2054, 10, 20, 12, tzinfo=UTC)
TROUBLE = (
    454,
    b"4.7.0 Cannot authenticate due to a temporary system problem. Try again later.",
)
LOGINS = (454, b"4.7.0 Too many login attempts, please try again later. - gsmtp")


def test_auth_temporary_system_problem_is_an_outage_not_a_rate_limit():
    """Only Gmail's login-rate wording is a healthy limit; its outage wording is not."""
    assert sending_limit(LOGINS, stage="auth") == "rate"
    assert sending_limit(TROUBLE, stage="auth") is None
    # The same wording never changes RCPT or DATA classification.
    assert sending_limit((421, TROUBLE[1]), stage="rcpt") == "rate"


def throttled(count, refused=None):
    """A TRANSIENT result whose ``refused`` positions (default all) were refused."""
    positions = tuple(range(count)) if refused is None else refused
    return FamilyDeliveryResult(Status.TRANSIENT, count, transient=positions)


@pytest.mark.parametrize(
    "attempt,seconds", [(1, 900), (2, 3600), (3, 14400), (4, 43200), (5, 43200)]
)
def test_every_recipient_throttled_backs_off_for_hours(attempt, seconds):
    """Five attempts then cover about 17 hours instead of about 8 minutes."""
    assert dispatch.result_retry_seconds(throttled(2), attempt) == seconds
    assert dispatch.result_retry_seconds(throttled(1), attempt) == seconds


def test_other_retries_keep_the_ordinary_schedule():
    """One of two addresses throttled, or an outage, still backs off as before."""
    partial = throttled(2, refused=(1,))
    outage = FamilyDeliveryResult(
        Status.UNAVAILABLE, 2, health=ProviderHealth.UNAVAILABLE
    )
    for result in (partial, outage):
        assert [dispatch.result_retry_seconds(result, n) for n in (1, 2, 5)] == [
            30,
            60,
            480,
        ]
    alert = SimpleNamespace(limit=None, status=Status.TRANSIENT, recipient_count=1)
    alert.transient = (0,)
    assert dispatch.result_retry_seconds(alert, 1) == 30


def limited():
    """A healthy Gmail rate-limit refusal."""
    return FamilyDeliveryResult(
        Status.TRANSIENT, 1, health=ProviderHealth.HEALTHY, limit="rate"
    )


@pytest.fixture
def history(monkeypatch):
    """Drive budget_spent with a chosen limit history, clock and acceptances."""
    logged = []
    state = SimpleNamespace(history=(0, None, None), accepted=True)
    monkeypatch.setattr(dispatch, "limit_history", lambda message: state.history)
    monkeypatch.setattr(dispatch, "database_now", lambda: NOW)
    monkeypatch.setattr(dispatch, "accepted_since", lambda instant: state.accepted)
    monkeypatch.setattr(
        dispatch, "_log_give_up", lambda message, waited, why: logged.append(waited)
    )
    state.logged = logged
    return state


def test_the_absolute_cap_counts_from_the_first_outcome_across_outages(history):
    """An outage that restarted the limit run cannot stretch the wait past 7 days."""
    message = SimpleNamespace(attempt=3)
    # The current limit run began a day ago, the first outcome 8 days ago.
    history.history = (2, NOW - timedelta(days=1), NOW - timedelta(days=8))
    assert dispatch.budget_spent(message, limited())
    assert history.logged == [timedelta(days=8)]


def test_a_recent_limit_run_inside_the_cap_keeps_waiting(history):
    """Within 7 days of the first outcome, flowing mail keeps the message waiting."""
    message = SimpleNamespace(attempt=3)
    history.history = (2, NOW - timedelta(days=3), NOW - timedelta(days=6))
    assert not dispatch.budget_spent(message, limited())
    assert history.logged == []
