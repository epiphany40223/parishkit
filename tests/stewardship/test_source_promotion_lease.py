"""A source promotion commits longer leases before its transaction (#386, M4).

The promotion's statement budget (120 s) is longer than what is left of the
60-second task and source leases, and the renewal thread waits on the
control lock the promotion's effect holds. The collaborators are stand-ins
here; the real leases are renewed by every PostgreSQL refresh test that
promotes.
"""

from contextlib import contextmanager
from types import SimpleNamespace

from parishkit.stewardship.jobs.lifetime import ExecutionControl
from parishkit.stewardship.source import execution as source_execution


def test_the_longer_lease_is_committed_before_the_promotion_effect(monkeypatch):
    """extend_lease(300) runs, and returns, before the effect opens."""
    order = []
    snapshot = SimpleNamespace(pk=1, state="staged", counts={"families": 3})
    monkeypatch.setattr(
        source_execution,
        "load_and_stage_attempt",
        lambda *args, **kwargs: snapshot,
    )
    monkeypatch.setattr(source_execution, "_retire_drained_staging", lambda *args: None)
    control = ExecutionControl()

    def owned():
        """Whether this thread holds the control lock (an RLock)."""
        return control.lock._is_owned()

    monkeypatch.setattr(
        source_execution,
        "extend_lease",
        lambda execution, seconds: order.append(("lease", seconds, owned())),
    )
    monkeypatch.setattr(
        source_execution.SourceRefreshAttempt.objects,
        "get",
        lambda **kwargs: SimpleNamespace(pk=2),
    )

    def promote(pk, claim, *, admit, reconcile):
        order.append(("promote", owned()))
        return "promoted"

    monkeypatch.setattr(source_execution, "promote_snapshot", promote)

    @contextmanager
    def effect():
        order.append("effect")
        yield
        order.append("committed")

    execution = SimpleNamespace(
        progress=lambda *args, **kwargs: order.append("progress"),
        effect=effect,
        control=control,
    )
    result = source_execution._observe(execution, "claim", "credential", None)
    assert result == "promoted"
    # The control lock is held from the extension through the promotion, so
    # no renewal (which takes that lock) can slip in between.
    assert order == [
        "progress",
        ("lease", source_execution.PROMOTION_LEASE_SECONDS, True),
        "effect",
        ("promote", True),
        "committed",
    ]
    assert not owned()
    assert source_execution.PROMOTION_LEASE_SECONDS == 300


def test_an_unchanged_refresh_promotes_nothing_and_extends_nothing(monkeypatch):
    """A quick update recorded as unchanged returns before any lease work."""
    snapshot = SimpleNamespace(state="unchanged")
    monkeypatch.setattr(
        source_execution, "load_and_stage_attempt", lambda *a, **k: snapshot
    )
    monkeypatch.setattr(source_execution, "_retire_drained_staging", lambda *a: None)
    extended = []
    monkeypatch.setattr(source_execution, "extend_lease", lambda *a: extended.append(a))
    assert source_execution._observe(None, "claim", "cred", None) is snapshot
    assert extended == []
