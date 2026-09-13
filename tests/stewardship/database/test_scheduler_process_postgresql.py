"""The operational loop retains its real singleton session through every pass."""

from threading import Event
from unittest.mock import Mock

import pytest
from django.db import connection

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import processes
from parishkit.stewardship.jobs.broker import BrokerRuntime
from parishkit.stewardship.jobs.dispatch import Handler, WorkQueue
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.scheduler import (
    SchedulerOwnershipLost,
    scan_once,
    scheduler_session,
)

from .test_dispatch_postgresql import queued

pytestmark = pytest.mark.django_db(transaction=True)


def handlers():
    """Only a synthetic task, with no provider implementation or runtime authority."""
    return {
        "dispatch_probe": Handler(
            WorkQueue.GENERAL, lambda *args: True, lambda *args: None
        )
    }


def test_loop_produces_then_publishes_without_transactions(monkeypatch):
    """A completed scan does not drop singleton ownership by closing the session."""
    stop, seen, generated = Event(), [], []

    def produce(guard):
        """Durably create a synthetic operation before scanning the same pass."""
        assert not connection.in_atomic_block
        guard.check()
        generated.append(queued().run_id)

    def publish(runtime, hint):
        """Network boundary retains the singleton but no row locks/transaction."""
        assert not connection.in_atomic_block
        seen.append(hint.run_id)

    monkeypatch.setattr(processes, "publish_hint", publish)
    runtime = BrokerRuntime(Mock(), ServiceRole.SCHEDULER)
    assert (
        processes.serve_scheduler(
            runtime,
            handlers=handlers(),
            lease=Mock(),
            stop=stop,
            heartbeat=stop.set,
            produce=produce,
        )
        == 0
    )
    assert seen == generated and TaskRun.objects.get().state == "queued"
    runtime.app.close.assert_called_once()
    with scheduler_session() as guard:
        guard.check()


def test_scheduler_loss_is_fatal_even_if_a_producer_reconnects():
    """The next owning boundary cannot interpret a new connection as still owned."""
    stop = Event()

    def reconnect(guard):
        """Simulate an outage followed by an ordinary query's automatic reconnect."""
        guard.check()
        connection.close()
        connection.ensure_connection()

    runtime = BrokerRuntime(Mock(), ServiceRole.SCHEDULER)
    with pytest.raises(SchedulerOwnershipLost):
        processes.serve_scheduler(
            runtime,
            handlers=handlers(),
            lease=Mock(),
            stop=stop,
            heartbeat=Mock(),
            produce=reconnect,
        )
    runtime.app.close.assert_called_once()


def test_stop_during_a_page_leaves_remaining_hints_for_next_scheduler():
    """Warm shutdown does not publish the rest of an already-selected page."""
    first, second, stop, seen = queued(), queued(), Event(), []

    def publish(hint):
        """Simulate SIGTERM arriving during the first bounded publication."""
        seen.append(hint.run_id)
        stop.set()

    with scheduler_session() as guard:
        result = scan_once(guard, handlers=handlers(), publish=publish, stop=stop)
        assert result.published == 1 and result.cursor is None
    assert seen == [first.run_id]
    assert TaskRun.objects.get(pk=second.run_id).state == "queued"
