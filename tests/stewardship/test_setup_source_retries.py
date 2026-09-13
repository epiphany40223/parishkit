"""Initial and final setup share bounded read-only recovery policy."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from parishkit.stewardship.source import setup_admission, setup_final_tasks


@pytest.mark.parametrize("final", [False, True])
@pytest.mark.parametrize(
    "attempt,seconds", [(1, 30), (2, 60), (4, 240), (5, None), (6, None)]
)
def test_abandoned_setup_reads_stop_after_five_attempts(
    monkeypatch, final, attempt, seconds
):
    """Drainage and original scope precede either retry or terminal failure."""
    owner = setup_final_tasks if final else setup_admission
    monkeypatch.setattr(owner, "source_available", Mock(return_value=True))
    monkeypatch.setattr(
        owner, "bound_preparation" if final else "bound_attempt", Mock()
    )
    monkeypatch.setattr(
        owner, "require_final_task" if final else "require_live_setup", Mock()
    )
    status = SimpleNamespace(state="abandoned", attempt=attempt)
    options = {"store": object()} if final else {}
    result = owner.recovery_plan(status, **options)
    assert result.action == ("recovery_fail" if seconds is None else "recovery_retry")
    if seconds is not None:
        assert result.retry_seconds == seconds


@pytest.mark.parametrize("final", [False, True])
def test_expired_setup_cancels_even_after_retry_budget_exhaustion(monkeypatch, final):
    """An expired original login cannot be reclassified as a provider failure."""
    owner = setup_final_tasks if final else setup_admission
    monkeypatch.setattr(owner, "source_available", Mock(return_value=True))
    monkeypatch.setattr(
        owner, "bound_preparation" if final else "bound_attempt", Mock()
    )
    monkeypatch.setattr(
        owner,
        "require_final_task" if final else "require_live_setup",
        Mock(side_effect=PermissionError("Expired original setup")),
    )
    options = {"store": object()} if final else {}
    assert (
        owner.recovery_plan(
            SimpleNamespace(state="abandoned", attempt=5), **options
        ).action
        == "recovery_cancel"
    )
