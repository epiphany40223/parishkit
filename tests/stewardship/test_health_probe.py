"""Monitoring remains bounded under hangs, concurrent scrapes and stale results."""

import json
from threading import Event

from parishkit.stewardship import installer_health
from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.health_probe import HealthProbe


def test_hung_probe_has_one_flight_and_other_callers_do_not_wait():
    """Repeated scrapes cannot create more threads for a stuck dependency."""
    release, entered = Event(), Event()
    probe, calls = HealthProbe(timeout=0.01), []

    def blocked():
        """Model uncancellable IO, then permit deterministic test cleanup."""
        calls.append(True)
        entered.set()
        release.wait(5)
        return "ready"

    try:
        assert probe.read(blocked, unavailable="down") == "down"
        assert entered.is_set()
        for _ in range(10):
            assert probe.read(blocked, unavailable="down") == "down"
        assert calls == [True]
    finally:
        release.set()


def test_probe_caches_success_and_sanitized_failure():
    """A private exception is never retained in or exposed from observations."""
    probe = HealthProbe()
    assert probe.read(lambda: "ready", unavailable="down") == "ready"
    assert probe.read(lambda: "unexpected", unavailable="down") == "ready"
    failure = HealthProbe()

    def broken():
        raise ValueError("private provider error")

    assert failure.read(broken, unavailable="down") == "down"
    assert failure.value == "down"


def test_installer_heartbeat_refuses_stale_or_reused_process(tmp_path, monkeypatch):
    """A marker alone is neither process identity nor proof of loop progress."""
    monkeypatch.setattr(installer_health, "DIRECTORY", tmp_path)
    monkeypatch.setattr(installer_health, "HEARTBEAT", tmp_path / "heartbeat.json")
    monkeypatch.setattr(installer_health, "process_identity", lambda pid: (1, 123))
    monkeypatch.setattr(installer_health, "monotonic", lambda: 1000)
    assert installer_health.healthcheck() == 1
    installer_health.publish_heartbeat()
    assert installer_health.healthcheck() == 0
    monkeypatch.setattr(installer_health, "monotonic", lambda: 1091)
    assert installer_health.healthcheck() == 1
    monkeypatch.setattr(installer_health, "monotonic", lambda: 1001)
    monkeypatch.setattr(installer_health, "process_identity", lambda pid: (1, 124))
    assert installer_health.healthcheck() == 1
    for value in ({}, {"pid": 12, "started": True, "time": 1000}):
        write_private(tmp_path / "heartbeat.json", json.dumps(value).encode())
        assert installer_health.healthcheck() == 1
