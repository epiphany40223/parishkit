"""Shared mail outages have finite probes rather than exhausting every Family."""

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
