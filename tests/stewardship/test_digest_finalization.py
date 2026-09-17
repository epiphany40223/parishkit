"""Completion proof failures never prevent fencing or durable task failure."""

from types import SimpleNamespace

import pytest

from parishkit.stewardship.reports import digest_finalization as finalization


@pytest.fixture
def invalid_completion(monkeypatch):
    """Isolate mutable outcome failure from separately tested SQL task binding."""
    monkeypatch.setattr(finalization, "_preparation", lambda status: None)

    def unavailable(status):
        """Simulate an unavailable completion proof, not forged task ownership."""
        raise PermissionError("Completion proof differs.")

    monkeypatch.setattr(finalization, "_outcome", unavailable)


@pytest.mark.parametrize(
    "action",
    ["lease_expired", "recovery_hint", "permanent_failure", "retryable_failure"],
)
def test_failed_proof_still_allows_owner_fencing_and_failure(
    invalid_completion, action
):
    assert finalization.admit_finalization(action, SimpleNamespace(state="running"))


def test_abandoned_invalid_proof_fails_without_restarting_effects(invalid_completion):
    status = SimpleNamespace(state="abandoned")
    assert finalization.recover_finalization(status).action == "recovery_fail"
    assert finalization.admit_finalization("recovery_hint", status)
    assert finalization.admit_finalization("recovery_fail", status)
    assert not finalization.admit_finalization("recovery_retry", status)
    assert finalization.recover_finalization(SimpleNamespace(state="running")) is None


def test_expiry_does_not_bypass_persisted_task_binding(monkeypatch):
    """The recovery exception permits fencing, never an unbound caller identity."""

    def unbound(status):
        """Reject a stale or forged persisted Task claim."""
        raise PermissionError("Task binding differs.")

    monkeypatch.setattr(finalization, "_preparation", unbound)
    with pytest.raises(PermissionError, match="Task binding"):
        finalization.admit_finalization(
            "lease_expired", SimpleNamespace(state="running")
        )
