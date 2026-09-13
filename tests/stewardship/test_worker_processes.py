"""Closed process options and cleanup, without real services or deployment files."""

from threading import Event
from unittest.mock import Mock

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.broker import BrokerRuntime
from parishkit.stewardship.jobs.processes import serve_consumer, worker_options


@pytest.mark.parametrize(
    "role,queues",
    [
        (ServiceRole.WORKER, ["general", "restore-general"]),
        (ServiceRole.MAIL_DISPATCH, ["mail-dispatch", "restore-mail"]),
        (ServiceRole.BACKUP_WORKER, ["backup-worker", "restore-backup"]),
    ],
)
def test_worker_options_cannot_add_queues_or_network_control(role, queues):
    """The process receives the same closed queue set as its broker identity."""
    options = worker_options(BrokerRuntime(Mock(), role))
    assert options["queues"] == queues
    assert options["pool"] == "solo" and options["concurrency"] == 1
    assert options["without_gossip"] and options["without_mingle"]
    assert options["without_heartbeat"] and not options["beat"]
    assert options["statedb"] is None and options["pidfile"] is None


@pytest.mark.parametrize(
    "runtime", [None, BrokerRuntime(Mock(), ServiceRole.SCHEDULER)]
)
def test_nonconsumer_cannot_start_worker(runtime):
    """Producer authority never also authorizes executing provider tasks."""
    with pytest.raises(ConfigError):
        worker_options(runtime)


def test_consumer_and_dispatch_must_share_the_same_stop_event():
    """An unconnected signal event could allow a draining worker to start more I/O."""
    with pytest.raises(ConfigError):
        serve_consumer(
            BrokerRuntime(Mock(), ServiceRole.WORKER, Event()),
            lease=Mock(),
            stop=Event(),
            heartbeat=Mock(),
        )


def test_pre_stopped_consumer_does_not_construct_worker_and_closes_app(monkeypatch):
    """Shutdown during admission does not open consumer sockets afterward."""
    from celery.worker import worker

    constructor, stop = Mock(), Event()
    monkeypatch.setattr(worker, "WorkController", constructor)
    stop.set()
    runtime = BrokerRuntime(Mock(), ServiceRole.WORKER, stop)
    assert serve_consumer(runtime, lease=Mock(), stop=stop, heartbeat=Mock()) == 0
    constructor.assert_not_called()
    runtime.app.close.assert_called_once()


def test_controller_start_failure_restores_app_and_signal_state(monkeypatch):
    """A failed initialization cannot leave process-global Celery state changed."""
    import signal

    from celery import _state
    from celery.worker import worker

    from .test_broker import broker

    stop, runtime = Event(), broker(ServiceRole.WORKER)
    runtime = BrokerRuntime(runtime.app, runtime.service, stop)
    previous_current = getattr(_state._tls, "current_app", None)
    previous_default = _state.default_app
    handler = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(worker, "WorkController", Mock(side_effect=RuntimeError))
    with pytest.raises(RuntimeError):
        serve_consumer(runtime, lease=Mock(), stop=stop, heartbeat=Mock())
    assert getattr(_state._tls, "current_app", None) is previous_current
    assert _state.default_app is previous_default
    assert signal.getsignal(signal.SIGTERM) is handler and stop.is_set()
