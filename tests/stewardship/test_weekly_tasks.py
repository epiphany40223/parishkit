"""Weekly task recovery retains finite attempts and rejects unbound actions."""

from types import SimpleNamespace

import pytest

from parishkit.stewardship.reports import weekly_tasks


@pytest.fixture
def owner(monkeypatch):
    """Isolate pure recovery/admission branches from separately tested SQL owners."""
    monkeypatch.setattr(weekly_tasks, "bound_preparation", lambda status: object())
    monkeypatch.setattr(weekly_tasks, "disposition", lambda row: None)


@pytest.mark.parametrize("attempt,delay", [(1, 60), (2, 120), (3, 240), (4, 480)])
def test_abandoned_preparation_retries_with_bounded_backoff(owner, attempt, delay):
    plan = weekly_tasks.recover_weekly(
        SimpleNamespace(state="abandoned", attempt=attempt)
    )
    assert plan.action == "recovery_retry" and plan.retry_seconds == delay


def test_exhausted_preparation_fails_visibly(owner):
    assert (
        weekly_tasks.recover_weekly(
            SimpleNamespace(state="abandoned", attempt=5)
        ).action
        == "recovery_fail"
    )
    assert (
        weekly_tasks.recover_weekly(SimpleNamespace(state="running", attempt=5)) is None
    )


@pytest.mark.parametrize(
    "outcome,action",
    [("complete", "recovery_complete"), ("safe_cancel", "recovery_cancel")],
)
def test_recovery_uses_durable_terminal_phase(owner, monkeypatch, outcome, action):
    monkeypatch.setattr(weekly_tasks, "disposition", lambda row: outcome)
    assert (
        weekly_tasks.recover_weekly(
            SimpleNamespace(state="abandoned", attempt=1)
        ).action
        == action
    )


def test_temporary_gate_does_not_forge_recovery_or_prevent_expiry(owner, monkeypatch):
    def held(row):
        """A temporary policy gate forbids new effects without losing the root."""
        raise PermissionError("Synthetic temporary gate")

    monkeypatch.setattr(weekly_tasks, "disposition", held)
    status = SimpleNamespace(state="abandoned", attempt=1)
    assert weekly_tasks.recover_weekly(status) is None
    assert weekly_tasks.admit_weekly("lease_expired", status)
    assert not weekly_tasks.admit_weekly("recovery_hint", status)


@pytest.mark.parametrize(
    "action",
    [
        "effect",
        "claim",
        "heartbeat",
        "hint",
        "progress",
        "explicit_retry",
        "explicit_retry_replay",
        "permanent_failure",
        "retryable_failure",
    ],
)
def test_live_preparation_admits_only_known_owned_actions(owner, action):
    status = SimpleNamespace(state="running", attempt=1)
    assert weekly_tasks.admit_weekly(action, status)
    assert not weekly_tasks.admit_weekly("invented_action", status)
    assert not weekly_tasks.admit_weekly("complete", status)
    assert not weekly_tasks.admit_weekly("safe_cancel", status)


def test_recovery_action_must_match_exact_plan(owner):
    status = SimpleNamespace(state="abandoned", attempt=2)
    assert weekly_tasks.admit_weekly("recovery_retry", status)
    assert not weekly_tasks.admit_weekly("recovery_complete", status)


@pytest.mark.parametrize(
    "arguments", [{}, {"public_origin": 1}, {"scheduler": "yes"}, {"scheduler": False}]
)
def test_handler_requires_compiled_role_and_origin(arguments):
    with pytest.raises(TypeError, match="runtime role and origin"):
        weekly_tasks.weekly_handler(**arguments)


def test_scheduler_handler_has_no_private_execution_authority():
    owner = weekly_tasks.weekly_handler(scheduler=True)
    with pytest.raises(PermissionError, match="scheduler cannot prepare"):
        owner.execute(object())
