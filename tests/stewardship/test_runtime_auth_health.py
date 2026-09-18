"""Periodic observations respect child lifetime, finite waits and private errors."""

import os
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import runtime_auth_health as health
from parishkit.stewardship import runtime_process


def observer(**values):
    """Build the actual loop with explicitly substituted application boundaries."""
    return health.PeriodicAuthenticationHealth(
        object(),
        check=values.get("check", Mock()),
        active=values.get("active", Mock(return_value=True)),
        retire=values.get("retire", Mock()),
    )


def test_failed_probe_retries_at_fixed_cadence_without_private_logs(
    monkeypatch, caplog
):
    """A failed pass does not kill the loop or manufacture a success observation."""
    value = observer()
    monkeypatch.setattr(health, "randbelow", lambda bound: bound - 1)
    wait = Mock(side_effect=[False, False, False, True])
    monkeypatch.setattr(value.stop, "wait", wait)
    sample = Mock(side_effect=[RuntimeError("private-token-canary"), None])
    monkeypatch.setattr(health, "observe_once", sample)
    value.run()
    assert wait.call_args_list[0].args == (30,)
    assert wait.call_args_list[1:] == [((30,),)] * 3
    assert sample.call_count == value.check.call_count == 2
    value.retire.assert_not_called()
    assert "private-token-canary" not in caplog.text
    assert "authentication_health_observation_failed" in caplog.text


@pytest.mark.parametrize("lost_lease", [False, True])
def test_shutdown_or_lost_lease_never_starts_another_observation(
    monkeypatch, lost_lease
):
    """Retire an invalid child, but do not turn ordinary shutdown into an error."""
    value = observer(active=Mock(return_value=lost_lease))
    if lost_lease:
        value.check.side_effect = ConfigError("private-lease-path")
    monkeypatch.setattr(value.stop, "wait", Mock(return_value=False))
    sample = Mock()
    monkeypatch.setattr(health, "observe_once", sample)
    value.run()
    sample.assert_not_called()
    assert value.retire.call_count == int(lost_lease)


def test_one_blocked_probe_cannot_spawn_replacements_or_block_exit(monkeypatch):
    """Bound the application-owned thread even if a dependency ignores timeouts."""
    entered, release = Event(), Event()
    calls = []

    def blocked(limiter):
        """Model an uninterruptible dependency without permanently hanging pytest."""
        calls.append(limiter)
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(health, "observe_once", blocked)
    monkeypatch.setattr(health, "INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(health, "JOIN_SECONDS", 0.02)
    value = observer()
    value.start()
    try:
        assert entered.wait(2)
        value.close()
        assert value.stop.is_set() and value.thread.is_alive()
        assert value.thread.daemon and calls == [value.limiter]
    finally:
        release.set()
        value.thread.join(2)
    assert not value.thread.is_alive()
    assert calls == [value.limiter]


def test_idle_close_wakes_without_a_probe(monkeypatch):
    """Worker exit need not wait for the next thirty-second interval."""
    sample = Mock()
    monkeypatch.setattr(health, "observe_once", sample)
    value = observer()
    value.start()
    value.close()
    assert not value.thread.is_alive()
    sample.assert_not_called()


def test_worker_starts_observer_only_after_loaded_receipts(monkeypatch, settings):
    """The real Gunicorn hook binds the admitted child, not the prefork master."""
    limiter, lease, receipt = object(), Mock(), Mock()
    settings.STEWARDSHIP_AUTH_RUNTIME = SimpleNamespace(limiter=limiter)
    settings.STEWARDSHIP_WEB_LEASE = lease
    worker = SimpleNamespace(pid=os.getpid(), alive=True, handle_exit=Mock())
    constructed = []

    def create(actual, **kwargs):
        """Assert ordering before replacing the spawned thread in this hook test."""
        receipt.assert_called_once_with(worker)
        assert actual is limiter
        value = Mock(**kwargs)
        constructed.append(value)
        return value

    monkeypatch.setattr(runtime_process, "publish_worker_receipts", receipt)
    monkeypatch.setattr(health, "PeriodicAuthenticationHealth", create)
    runtime_process.admitted_worker_started(worker)
    assert worker.stewardship_auth_health is constructed[0]
    value = constructed[0]
    value.start.assert_called_once()
    value.check()
    lease.check.assert_called_once()
    assert value.active()
    worker.alive = False
    assert not value.active()
    value.retire()
    worker.handle_exit.assert_called_once_with(runtime_process.signal.SIGTERM, None)
    runtime_process.admitted_worker_exited(None, worker)
    value.close.assert_called_once()


def test_master_exit_hook_never_joins_child_observer():
    """A reap callback in the master must not use copied child runtime state."""
    value = Mock()
    runtime_process.admitted_worker_exited(
        None, SimpleNamespace(pid=os.getpid() + 1, stewardship_auth_health=value)
    )
    value.close.assert_not_called()


def test_exit_before_admission_and_close_failure_are_safe(caplog):
    """Partial startup and private cleanup errors do not escape Gunicorn hooks."""
    worker = SimpleNamespace(pid=os.getpid())
    runtime_process.admitted_worker_exited(None, worker)
    worker.stewardship_auth_health = Mock()
    worker.stewardship_auth_health.close.side_effect = ValueError("private-canary")
    runtime_process.admitted_worker_exited(None, worker)
    assert "private-canary" not in caplog.text
    assert "authentication_health_observation_failed" in caplog.text


def test_expected_limiter_failure_has_a_safe_dependency_category(caplog):
    """Routine unavailability is not mislabeled as an unexpected programming error."""
    import json

    from parishkit.stewardship.accounts.limiting import LimiterUnavailable
    from parishkit.stewardship.observability import SafeJsonFormatter

    health.emit_failure(
        LimiterUnavailable("private-canary"), event=health.LogEvent.AUTH_HEALTH_FAILED
    )
    value = json.loads(SafeJsonFormatter().format(caplog.records[-1]))
    assert value["extra"]["failure_kind"] == "authentication_limiter_unavailable"
    assert "private-canary" not in str(value)


@pytest.mark.parametrize("fail", [False, True])
def test_observation_limits_and_closes_its_thread_connection(monkeypatch, fail):
    """Session budgets are installed before any actual observation, on both paths."""
    from django.db import connection, connections

    cursor = Mock()
    context = Mock(__enter__=Mock(return_value=cursor), __exit__=Mock())
    monkeypatch.setattr(connection, "cursor", Mock(return_value=context))
    close = Mock()
    monkeypatch.setattr(connections, "close_all", close)
    limiter = Mock()
    if fail:
        limiter.observe_health.side_effect = RuntimeError("synthetic")
        with pytest.raises(RuntimeError):
            health.observe_once(limiter)
    else:
        health.observe_once(limiter)
    assert [call.args[0] for call in cursor.execute.call_args_list] == [
        "SET statement_timeout='2s'",
        "SET lock_timeout='1s'",
        "SET transaction_timeout='10s'",
    ]
    close.assert_called_once()
