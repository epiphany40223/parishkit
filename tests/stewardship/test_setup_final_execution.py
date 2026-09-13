"""Compiled setup handlers reject incomplete assembly and unintended execution."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from parishkit.stewardship.source import setup_final_execution as owner
from parishkit.stewardship.source.setup_final_production import produce_finalization
from parishkit.stewardship.storage import StorageInvariantError


@pytest.mark.parametrize("missing", ["credential_path", "general", "mac", "public"])
def test_final_worker_requires_each_installed_dependency(missing):
    """No worker is executable with a placeholder configuration or missing keyring."""
    values = dict(credential_path=Path("/synthetic/key"), general=1, mac=2, public=3)
    values[missing] = None
    with pytest.raises(TypeError, match="installed credentials"):
        owner.finalization_handler(object(), **values)


def test_scheduler_registry_never_executes_provider_work():
    """The scheduler can recover metadata but has no private execution entry point."""
    handler = owner.finalization_handler(object(), scheduler=True)
    with pytest.raises(PermissionError, match="scheduler"):
        handler.execute(object())


def test_worker_requires_maintained_execution():
    """Reject a naked invocation before acquiring any Task or source lease."""
    handler = owner.finalization_handler(
        object(), credential_path=Path("/synthetic/key"), general=1, mac=2, public=3
    )
    with pytest.raises(StorageInvariantError, match="maintained execution"):
        handler.execute(SimpleNamespace(control=SimpleNamespace(active=False)))


def test_producer_requires_real_scheduler_guard():
    """An arbitrary caller cannot turn a prepared receipt into queued work."""
    with pytest.raises(TypeError, match="scheduler ownership"):
        produce_finalization(object(), object())


def test_unknown_failure_propagates_for_existing_lease_recovery():
    """Unexpected bugs are not silently classified as safely drained source errors."""
    error = RuntimeError("synthetic unexpected failure")
    with pytest.raises(RuntimeError) as raised:
        owner._failed(object(), error, None, store=object())
    assert raised.value is error
