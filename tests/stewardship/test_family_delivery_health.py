"""Shared mail outages have finite probes rather than exhausting every Family."""

from pathlib import Path

import pytest

from parishkit.stewardship.family_delivery import ProviderHealth as Health
from parishkit.stewardship.jobs import family_mail_delivery_tasks as worker


def test_repeated_shared_outage_halts_run_after_three_spaced_probes(monkeypatch):
    """The circuit cools down before probing and stops before a five-try budget."""
    clock = [100.0]
    monkeypatch.setattr(worker, "monotonic", lambda: clock[0])
    health = worker.DeliveryCircuit()
    for number in range(1, 4):
        assert not health.blocks_new_send()
        assert health.observe(Health.UNAVAILABLE) is (number == 3)
        assert health.blocks_new_send()
        clock[0] += 60
    assert health.blocks_new_send()
    assert health.observe(Health.SYSTEMIC) is False
    health.observe(Health.HEALTHY)
    assert health.blocks_new_send()  # A concurrent receipt must never undo a halt.


@pytest.mark.parametrize("systemic", [False, True])
def test_operational_cooldown_admits_recovered_provider(monkeypatch, systemic):
    """Both trip conditions eventually permit a probe without a process restart."""
    clock = [100.0]
    monkeypatch.setattr(worker, "monotonic", lambda: clock[0])
    health = worker.DeliveryCircuit(recovery_seconds=300)
    if systemic:
        assert health.observe(Health.SYSTEMIC)
    else:
        for attempt in range(3):
            assert not health.blocks_new_send()
            assert health.observe(Health.UNAVAILABLE) is (attempt == 2)
            if attempt < 2:
                clock[0] += 60
    assert health.blocks_new_send()
    health.observe(Health.UNOBSERVED)
    clock[0] += 299
    assert health.blocks_new_send()
    clock[0] += 1
    assert not health.blocks_new_send()
    assert not health.observe(Health.HEALTHY)
    assert not health.blocks_new_send() and health.failures == 0


@pytest.mark.parametrize("seconds", [True, 0, 59, 3601, "300"])
def test_invalid_cooldown_is_rejected(seconds):
    """An accidental infinite or zero-loop cooldown must fail during assembly."""
    with pytest.raises(ValueError):
        worker.DeliveryCircuit(recovery_seconds=seconds)


def test_operational_handler_opts_into_finite_recovery():
    """The actual compiled operational owner, not just a spare helper, can recover."""
    from parishkit.stewardship.jobs.operational_mail_tasks import delivery_handler

    handler = delivery_handler(None, credential_path=Path("/synthetic/not-read"))
    assert handler.admit.keywords["circuit"].recovery_seconds == 300


def test_non_shared_outcome_resets_consecutive_probe_budget(monkeypatch):
    """A healthy connection with recipient-specific refusal resets shared faults."""
    monkeypatch.setattr(worker, "monotonic", lambda: 100.0)
    health = worker.DeliveryCircuit()
    assert health.observe(Health.UNAVAILABLE) is False
    assert health.blocks_new_send()
    assert health.observe(Health.UNOBSERVED) is False
    assert health.blocks_new_send() and health.failures == 1
    assert health.observe(Health.HEALTHY) is False
    assert not health.blocks_new_send() and health.failures == 0
    assert health.observe(Health.SYSTEMIC) is True
    assert health.blocks_new_send()
