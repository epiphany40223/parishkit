"""A repeatedly failing setup cleanup becomes visible terminal work, not a loop."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from parishkit.stewardship.source import setup_cleanup as cleanup


@pytest.mark.parametrize("attempt", [1, 2, 4, 5, 6])
@pytest.mark.parametrize("drained", [False, True])
def test_cleanup_recovery_is_bounded_and_waits_for_real_drain(
    monkeypatch, attempt, drained
):
    """Unknown failures keep normal fencing; exhaustion never retries forever."""
    status = SimpleNamespace(state="abandoned", attempt=attempt)
    monkeypatch.setattr(cleanup, "_attempt", lambda *args, **kwargs: object())
    monkeypatch.setattr(cleanup, "source_available", lambda: drained)
    query = Mock()
    query.exists.return_value = True
    monkeypatch.setattr(
        cleanup.SystemConfiguration.objects, "filter", lambda **kwargs: query
    )
    plan = cleanup.recover_cleanup(status)
    if not drained:
        assert plan is None
    elif attempt >= 5:
        assert plan.action == "recovery_fail" and plan.retry_seconds is None
    else:
        assert plan.action == "recovery_retry"
        assert plan.retry_seconds == 30 * 2 ** (attempt - 1)
    for action in ("recovery_hint", "recovery_retry", "recovery_fail"):
        assert cleanup.admit_cleanup(action, status) is (
            plan is not None and action in {"recovery_hint", plan.action}
        )
