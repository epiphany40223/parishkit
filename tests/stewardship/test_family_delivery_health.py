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


def test_family_handler_recovers_from_outages_but_not_systemic_faults():
    """The compiled Family owner pauses for an outage and stops for SYSTEMIC."""
    handler = worker.delivery_handler(None, credential_path=Path("/synthetic/x"))
    circuit = handler.admit.keywords["circuit"]
    assert circuit.recovery_seconds == worker.OUTAGE_RECOVERY_SECONDS == 600
    assert circuit.systemic_stops


@pytest.mark.parametrize("systemic_during_pause", [False, True])
def test_an_outage_pause_lifts_unless_a_systemic_fault_stops_it(
    monkeypatch, caplog, systemic_during_pause
):
    """Three outages pause sending for the cooldown; a SYSTEMIC fault stops it."""
    clock = [100.0]
    monkeypatch.setattr(worker, "monotonic", lambda: clock[0])
    health = worker.DeliveryCircuit(recovery_seconds=600, systemic_stops=True)
    for attempt in range(3):
        assert not health.blocks_new_send()
        assert health.observe(Health.UNAVAILABLE) is (attempt == 2)
        clock[0] += 60
    assert health.blocks_new_send() and not health.stopped
    if systemic_during_pause:
        # An in-flight result turns the pause into a stop, reported once.
        assert health.observe(Health.SYSTEMIC) is True
        assert health.observe(Health.SYSTEMIC) is False
    clock[0] += 600
    assert health.blocks_new_send() is systemic_during_pause
    assert health.stopped is systemic_during_pause
    resumed = "sending resumes" in caplog.text
    assert resumed is not systemic_during_pause


def test_a_systemic_fault_stops_until_restart(monkeypatch):
    """No cooldown lifts a configuration fault: waiting cannot fix it."""
    clock = [100.0]
    monkeypatch.setattr(worker, "monotonic", lambda: clock[0])
    health = worker.DeliveryCircuit(recovery_seconds=600, systemic_stops=True)
    assert health.observe(Health.SYSTEMIC) is True
    clock[0] += 3600 * 24
    assert health.blocks_new_send() and health.stopped


def test_a_failed_probe_pauses_again_at_once(monkeypatch):
    """After a pause one probe is admitted; its outage result pauses again.

    Only the first pause since a healthy result is reported as new
    (``repeated`` stays false), so a long outage alerts once.
    """
    clock = [100.0]
    monkeypatch.setattr(worker, "monotonic", lambda: clock[0])
    health = worker.DeliveryCircuit(recovery_seconds=600, systemic_stops=True)
    for _ in range(3):
        health.observe(Health.UNAVAILABLE)
        clock[0] += 60
    assert health.halted.is_set() and not health.repeated
    for _ in range(3):
        clock[0] += 600
        assert not health.blocks_new_send()
        assert health.observe(Health.UNAVAILABLE) is True
        assert health.blocks_new_send() and health.repeated
    clock[0] += 600
    assert not health.blocks_new_send()
    assert health.observe(Health.HEALTHY) is False
    for _ in range(3):
        health.observe(Health.UNAVAILABLE)
        clock[0] += 60
    # A new outage after a healthy result is reported as new again.
    assert health.halted.is_set() and not health.repeated


def test_sender_state_names_each_state_and_when_it_ends(monkeypatch, tmp_path):
    """ADM-13: a consumer's status record reports its sender in fixed words."""
    clock = [100.0]
    monkeypatch.setattr(worker, "monotonic", lambda: clock[0])
    marker = tmp_path / "stop"
    health = worker.DeliveryCircuit(
        recovery_seconds=600, systemic_stops=True, shared_stop=marker
    )
    assert health.sender_state() == ("running", None)
    health.note_capped(True)
    assert health.sender_state() == ("daily_limit", None)
    health.note_capped(False)
    health.hold(120)
    assert health.sender_state() == ("gmail_held", 120)
    clock[0] += 120
    assert health.sender_state() == ("running", None)
    for _ in range(3):
        health.observe(Health.UNAVAILABLE)
    assert health.sender_state() == ("outage_paused", 600)
    clock[0] += 700
    assert health.sender_state() == ("outage_paused", 0.0)
    assert not health.blocks_new_send()
    assert health.sender_state() == ("running", None)
    # The other consumer's SYSTEMIC stop halts this one's report too.
    marker.write_bytes(b"stopped")
    assert health.sender_state() == ("halted", None)
    # Reporting only looks: the circuit follows the marker at its next
    # admission check, which is where the stop is logged.
    assert not health.stopped and not health.halted.is_set()
    assert health.blocks_new_send() and health.stopped


def test_the_family_sender_state_follows_this_process_s_circuit(monkeypatch):
    """No circuit yet reads as running; the built handler's circuit is used."""
    monkeypatch.setattr(worker, "FAMILY_CIRCUIT", None)
    assert worker.family_sender_state() == ("running", None)
    worker.delivery_handler(object(), credential_path=Path("key"))
    circuit = worker.FAMILY_CIRCUIT
    assert isinstance(circuit, worker.DeliveryCircuit)
    circuit.observe(Health.SYSTEMIC)
    assert worker.family_sender_state() == ("halted", None)
    # A scheduler's metadata-only handler never replaces the consumer's.
    worker.delivery_handler(object(), scheduler=True)
    assert worker.FAMILY_CIRCUIT is circuit
