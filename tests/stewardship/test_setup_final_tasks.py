"""Finalization recovery distinguishes original admission from safe drained exit."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.stewardship.source import setup_final_tasks as tasks


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("available", [False, True])
def test_finalization_recovery_preserves_source_drain(monkeypatch, live, available):
    """An expired login permits cancellation only after old external work drains."""
    status, store = SimpleNamespace(state="abandoned", attempt=1), object()
    monkeypatch.setattr(tasks, "bound_preparation", lambda _: None)
    monkeypatch.setattr(tasks, "source_available", lambda: available)

    def require(value, *, store):
        """Model original-login expiry without manufacturing recovery success."""
        assert value is status
        if not live:
            raise PermissionError("expired")

    monkeypatch.setattr(tasks, "require_final_task", require)
    plan = tasks.recovery_plan(status, store=store)
    if not available:
        assert plan is None
    else:
        assert plan.action == ("recovery_retry" if live else "recovery_cancel")
        assert plan.retry_seconds == (30 if live else None)


def test_finalization_recovery_rejects_unabandoned_state(monkeypatch):
    """A normal live task cannot use abandoned-work exceptions."""
    monkeypatch.setattr(tasks, "bound_preparation", lambda _: None)
    with pytest.raises(PermissionError, match="abandonment"):
        tasks.recovery_plan(SimpleNamespace(state="running"), store=object())


@pytest.mark.parametrize("action", ["lease_expired", "recovery_hint"])
def test_running_finalization_can_be_fenced_without_original_liveness(
    monkeypatch, action
):
    """Losing the original login does not strand an expired worker lease forever."""
    monkeypatch.setattr(tasks, "bound_preparation", lambda _: None)
    assert tasks.admit_finalization_task(
        action, SimpleNamespace(state="running"), store=object()
    )


@pytest.mark.parametrize(
    "action", ["safe_cancel", "permanent_failure", "retryable_failure"]
)
@pytest.mark.parametrize("owner", ["none", "self", "other"])
def test_finalization_failure_waits_for_source_release(monkeypatch, action, owner):
    """The outcome cannot make a still-owned source lease disappear implicitly."""
    monkeypatch.setattr(tasks, "bound_preparation", lambda _: None)
    status = SimpleNamespace(state="running", run_id=uuid4())
    owner_id = {"none": None, "self": status.run_id, "other": uuid4()}[owner]
    monkeypatch.setattr(
        tasks.SourceMutationLease.objects,
        "get",
        lambda **kwargs: SimpleNamespace(owner_id=owner_id),
    )
    assert tasks.admit_finalization_task(action, status, store=object()) is (
        owner != "self"
    )


@pytest.mark.parametrize(
    "action", ["safe_cancel", "permanent_failure", "retryable_failure"]
)
@pytest.mark.parametrize("owner", ["none", "self", "other"])
def test_initial_load_failure_does_not_wait_for_another_task(
    monkeypatch, action, owner
):
    """Pre-claim contention settles without releasing or waiting on the winner."""
    from parishkit.stewardship.source import setup_admission

    status = SimpleNamespace(state="running", run_id=uuid4())
    owner_id = {"none": None, "self": status.run_id, "other": uuid4()}[owner]
    monkeypatch.setattr(setup_admission, "bound_attempt", lambda _: None)
    monkeypatch.setattr(
        setup_admission.SourceMutationLease.objects,
        "get",
        lambda **kwargs: SimpleNamespace(owner_id=owner_id),
    )
    assert setup_admission.admit_setup_task(action, status) is (owner != "self")


@pytest.mark.parametrize(
    "action", ["recovery_hint", "recovery_retry", "recovery_cancel", "recovery_fail"]
)
@pytest.mark.parametrize(
    "plan", [None, "recovery_retry", "recovery_cancel", "recovery_fail"]
)
def test_finalization_recovery_admits_only_its_verified_disposition(
    monkeypatch, action, plan
):
    """A queue cannot substitute success or another outcome for the current plan."""
    monkeypatch.setattr(tasks, "bound_preparation", lambda _: None)
    monkeypatch.setattr(
        tasks,
        "recovery_plan",
        lambda *args, **kwargs: SimpleNamespace(action=plan) if plan else None,
    )
    assert tasks.admit_finalization_task(
        action, SimpleNamespace(state="abandoned"), store=object()
    ) is (plan is not None and action in {"recovery_hint", plan})
