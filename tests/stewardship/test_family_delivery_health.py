"""Shared mail outages have finite probes rather than exhausting every Family."""

from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs import family_mail_delivery_tasks as worker


def test_repeated_shared_outage_halts_run_after_three_spaced_probes(monkeypatch):
    """The circuit cools down before probing and stops before a five-try budget."""
    clock = [100.0]
    monkeypatch.setattr(worker, "monotonic", lambda: clock[0])
    health = worker.DeliveryHealth()
    for number in range(1, 4):
        assert not health.is_set()
        assert health.observe(Status.UNAVAILABLE) is (number == 3)
        assert health.is_set()
        clock[0] += 60
    assert health.is_set()
    assert health.observe(Status.SYSTEMIC) is False
    health.observe(Status.ACCEPTED)
    assert health.is_set()  # A concurrent receipt must never undo a halt.


def test_non_shared_outcome_resets_consecutive_probe_budget(monkeypatch):
    """A healthy connection with recipient-specific refusal resets shared faults."""
    monkeypatch.setattr(worker, "monotonic", lambda: 100.0)
    health = worker.DeliveryHealth()
    assert health.observe(Status.UNAVAILABLE) is False
    assert health.is_set()
    assert health.observe(Status.TRANSIENT) is False
    assert not health.is_set() and health.failures == 0
    assert health.observe(Status.SYSTEMIC) is True
    assert health.is_set()
