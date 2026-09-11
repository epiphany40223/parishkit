"""Container test polling requires actual evidence, never presumed readiness."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from . import runtime_health_checks as checks


@pytest.mark.parametrize("initially_ready", [False, True])
def test_cohort_wait_returns_only_a_successful_whole_worker_observation(
    monkeypatch, initially_ready
):
    """No fixed startup delay or first responding worker stands in for the cohort."""
    ready = SimpleNamespace(returncode=0, stdout="whole-cohort", stderr="")
    pending = SimpleNamespace(returncode=1, stdout="", stderr="partial-cohort")
    run = Mock(side_effect=[ready] if initially_ready else [pending, ready])
    monkeypatch.setattr(checks.time, "monotonic", Mock(side_effect=[0, 1]))
    sleep = Mock()
    monkeypatch.setattr(checks.time, "sleep", sleep)
    configuration = Path("/opt/synthetic/web.yaml")
    assert (
        checks.wait_for_consumer_cohort(
            "compose", "project", "probe", configuration, run
        )
        is ready
    )
    assert run.call_count == (1 if initially_ready else 2)
    assert sleep.call_count == (0 if initially_ready else 1)
    for call in run.call_args_list:
        assert call.kwargs == {"check": False, "timeout": 10}
        assert call.args[-2:] == ("probe", str(configuration))


def test_cohort_wait_fails_when_complete_evidence_never_arrives(monkeypatch):
    """Persistent disagreement remains a failed integration test at its deadline."""
    run = Mock(return_value=SimpleNamespace(returncode=1, stdout="", stderr="partial"))
    monkeypatch.setattr(checks.time, "monotonic", Mock(side_effect=[0, 1, 45]))
    monkeypatch.setattr(checks.time, "sleep", Mock())
    with pytest.raises(AssertionError, match="cohort did not become ready: partial"):
        checks.wait_for_consumer_cohort("compose", "project", "probe", "config", run)
    assert run.call_count == 2
