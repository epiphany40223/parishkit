"""Family mail retry rules from the #298 review follow-ups (#382)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from parishkit.stewardship.family_delivery import (
    FamilyDeliveryResult,
    ProviderHealth,
    _handshake,
    sending_limit,
)
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs import family_mail_delivery_tasks as worker
from parishkit.stewardship.jobs import family_mail_dispatch as dispatch
from parishkit.stewardship.jobs.family_mail_dispatch import LimitHistory

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


def test_auth_temporary_system_problem_counts_toward_the_outage_pause(monkeypatch):
    """Through the real AUTH handshake: three such replies pause sending."""
    clock = [100.0]
    monkeypatch.setattr(worker, "monotonic", lambda: clock[0])
    circuit = worker.DeliveryCircuit()
    limited = _handshake("auth", LOGINS, 235, 1)
    assert limited.limit == "rate" and limited.health is ProviderHealth.HEALTHY
    for number in range(1, 4):
        result = _handshake("auth", TROUBLE, 235, 1)
        assert result.status is Status.UNAVAILABLE and result.limit is None
        assert result.health is ProviderHealth.UNAVAILABLE
        assert circuit.observe(result.health) is (number == 3)
        clock[0] += 60
    assert circuit.blocks_new_send()


def throttled(count, refused=None):
    """A TRANSIENT result whose ``refused`` positions (default all) were refused."""
    positions = tuple(range(count)) if refused is None else refused
    return FamilyDeliveryResult(Status.TRANSIENT, count, transient=positions)


@pytest.mark.parametrize(
    "counted,seconds", [(1, 900), (2, 3600), (3, 14400), (4, 43200), (5, 43200)]
)
def test_every_recipient_throttled_backs_off_for_hours(counted, seconds):
    """Five attempts then cover about 17 hours instead of about 8 minutes."""
    for count in (1, 2):
        result = throttled(count)
        assert dispatch.result_retry_seconds(result, 9, counted=counted) == seconds


def test_the_throttle_step_counts_only_budget_attempts(monkeypatch):
    """Attempts spared by limits or outages do not advance the throttle step."""
    monkeypatch.setattr(
        dispatch, "limit_history", lambda message: LimitHistory(3, None, None, None)
    )
    # Attempt 4 after three spared limit refusals is the first counted one.
    assert dispatch.family_retry_seconds("m", 4, throttled(1)) == 900
    assert dispatch.family_retry_seconds("m", 5, throttled(1)) == 3600


def test_other_retries_keep_the_ordinary_schedule():
    """A partial or mixed refusal, or an outage, backs off as before."""
    partial = throttled(2, refused=(1,))
    mixed = FamilyDeliveryResult(Status.TRANSIENT, 2, permanent=(0,), transient=(1,))
    outage = FamilyDeliveryResult(
        Status.UNAVAILABLE, 2, health=ProviderHealth.UNAVAILABLE
    )
    for result in (partial, mixed, outage):
        assert [
            dispatch.result_retry_seconds(result, n, counted=n) for n in (1, 2, 5)
        ] == [30, 60, 480]


def test_alerts_never_get_the_throttle_schedule():
    """Administrator alerts pass a real Family result but no ``counted``."""
    import inspect

    from parishkit.stewardship.jobs import operational_dispatch

    assert dispatch.result_retry_seconds(throttled(1), 1) == 30
    source = inspect.getsource(operational_dispatch)
    assert "family_retry_seconds" not in source
    assert "result_retry_seconds(result, message.attempt)" in source


def limited():
    """A healthy Gmail rate-limit refusal."""
    return FamilyDeliveryResult(
        Status.TRANSIENT, 1, health=ProviderHealth.HEALTHY, limit="rate"
    )


@pytest.fixture
def history(monkeypatch):
    """Drive budget_spent with a chosen limit history, clock and acceptances."""
    logged = []
    state = SimpleNamespace(history=LimitHistory(0, None, None, None), accepted=True)
    monkeypatch.setattr(dispatch, "limit_history", lambda message: state.history)
    monkeypatch.setattr(dispatch, "database_now", lambda: NOW)
    monkeypatch.setattr(dispatch, "accepted_since", lambda instant: state.accepted)
    monkeypatch.setattr(
        dispatch, "_log_give_up", lambda message, waited, why: logged.append(waited)
    )
    state.logged = logged
    return state


def days(number):
    """``number`` days before NOW."""
    return NOW - timedelta(days=number)


def test_the_absolute_cap_counts_from_the_first_hold_across_outages(history):
    """An outage that restarted the limit run cannot stretch the wait past 7 days."""
    message = SimpleNamespace(attempt=3)
    # The current limit run began a day ago, the first limit or outage 8 days ago.
    history.history = LimitHistory(2, days(1), days(9), days(8))
    assert dispatch.budget_spent(message, limited())
    assert history.logged == [timedelta(days=8)]


def test_an_old_ordinary_outcome_does_not_fail_a_new_hold(history):
    """The cap counts from the first limit or outage outcome, not from any outcome."""
    message = SimpleNamespace(attempt=3)
    history.history = LimitHistory(1, days(1), days(20), days(1))
    assert not dispatch.budget_spent(message, limited())
    assert history.logged == []
