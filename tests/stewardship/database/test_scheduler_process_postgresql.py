"""The operational loop retains its real singleton session through every pass."""

from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import processes
from parishkit.stewardship.jobs.broker import BrokerRuntime
from parishkit.stewardship.jobs.dispatch import Handler, WorkQueue, claim_hint
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


def test_due_hints_are_published_before_the_producers_run(monkeypatch):
    """Already-due work is not held behind slow producers (#394).

    The producers take the global work-order lock one after another, so a
    source refresh can stretch them to tens of seconds. The already-queued
    task is published before they start, and not again after: the late scan
    skips a row it just published that has not changed since (#394).
    """
    stop, seen, order = Event(), [], []
    due = queued().run_id

    def produce(guard):
        """Record what was already published when the producers started."""
        order.append(list(seen))
        order.append(queued().run_id)

    def publish(runtime, hint):
        seen.append(hint.run_id)

    monkeypatch.setattr(processes, "publish_hint", publish)
    runtime = BrokerRuntime(Mock(), ServiceRole.SCHEDULER)
    processes.serve_scheduler(
        runtime,
        handlers=handlers(),
        lease=Mock(),
        stop=stop,
        heartbeat=stop.set,
        produce=produce,
    )
    produced = order[1]
    assert order[0] == [due]
    assert seen == [due, produced]
    # A duplicate hint is harmless anyway: only the first claim succeeds.
    arguments = dict(queue=WorkQueue.GENERAL, worker_id=uuid4(), handlers=handlers())
    assert claim_hint(due, **arguments) is not None
    assert claim_hint(due, **arguments) is None
    assert TaskRun.objects.get(pk=due).state == "running"


def test_a_failed_early_scan_still_runs_the_producers(monkeypatch):
    """A failure publishing before the producers is reported, not fatal to them."""
    stop, produced, scans, failures = Event(), [], [], []
    real = processes.scan_once

    def scan(guard, **kwargs):
        """Fail only the first, pre-producer scan."""
        scans.append(kwargs["cursor"])
        if len(scans) == 1:
            raise RuntimeError("synthetic scan failure")
        return real(guard, **kwargs)

    monkeypatch.setattr(processes, "scan_once", scan)
    monkeypatch.setattr(processes, "publish_hint", lambda *args: None)
    monkeypatch.setattr(processes, "emit_failure", failures.append)
    runtime = BrokerRuntime(Mock(), ServiceRole.SCHEDULER)
    processes.serve_scheduler(
        runtime,
        handlers=handlers(),
        lease=Mock(),
        stop=stop,
        heartbeat=stop.set,
        produce=lambda guard: produced.append(True),
    )
    assert produced == [True] and len(scans) == 2
    assert [str(error) for error in failures] == ["synthetic scan failure"]


def counted_work_locks(monkeypatch):
    """Count global work-order lock takes, keeping the real lock."""
    from parishkit.stewardship.campaigns import work_locks

    calls, real = [], work_locks.lock_work_order

    def counting():
        calls.append(1)
        return real()

    monkeypatch.setattr(work_locks, "lock_work_order", counting)
    return calls


def test_no_early_scan_while_a_backlog_sweep_is_in_progress(monkeypatch):
    """A backlog adds no scans or lock takes beyond one pass per row (#394).

    The handler uses the production Family mail handler's scope, so each due
    row costs a global-lock transaction, as in production. The early scan
    runs only when a sweep starts; mid-sweep, the loop goes straight to the
    producers, and the late scan continues the sweep.
    """
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import (
        delivery_handler,
    )

    scope = delivery_handler(Mock(), scheduler=True).scope
    registry = {
        "dispatch_probe": Handler(
            WorkQueue.GENERAL, lambda *args: True, lambda *args: None, scope=scope
        )
    }
    backlog = [queued().run_id for _ in range(250)]
    stop, seen, loops = Event(), [], []
    locks = counted_work_locks(monkeypatch)
    real = processes.scan_once

    def scan(guard, **kwargs):
        """Record each scan's cursor and the lock takes it cost."""
        before = len(locks)
        result = real(guard, **kwargs)
        loops[-1].append((kwargs["cursor"] is None, len(locks) - before))
        return result

    def produce(guard):
        """Mark where the producers ran within this loop."""
        loops[-1].append("produce")

    def heartbeat():
        """End after three loops, without real scheduler sleeps."""
        if len(loops) == 3:
            stop.set()
        else:
            loops.append([])

    loops.append([])
    monkeypatch.setattr(processes, "scan_once", scan)
    monkeypatch.setattr(
        processes, "publish_hint", lambda runtime, hint: seen.append(hint.run_id)
    )
    monkeypatch.setattr(stop, "wait", lambda timeout: stop.is_set())
    processes.serve_scheduler(
        BrokerRuntime(Mock(), ServiceRole.SCHEDULER),
        handlers=registry,
        lease=Mock(),
        stop=stop,
        heartbeat=heartbeat,
        produce=produce,
    )
    # Loop 1 starts the sweep with a capped early page, and its late scan
    # continues after it. Loops 2 and 3 are mid-sweep: no early scan. The
    # 250 rows are published once each, at one lock take per row.
    assert loops == [
        [(True, processes.EARLY_SCAN_ROWS), "produce", (False, 100)],
        ["produce", (False, 100)],
        ["produce", (False, 50 - processes.EARLY_SCAN_ROWS)],
    ]
    assert sorted(seen) == sorted(backlog)


def test_held_rows_cost_the_early_scan_at_most_its_cap(monkeypatch):
    """A paused send is checked every loop, but only once per row (#394).

    Refused rows are still checked under the global lock each sweep. With 80
    held rows, the early scan checks at most EARLY_SCAN_ROWS of them and the
    late scan continues after them, so each loop costs 80 lock takes, not
    80 plus a repeated page.
    """
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import (
        delivery_handler,
    )

    scope = delivery_handler(Mock(), scheduler=True).scope
    registry = {
        "dispatch_probe": Handler(
            WorkQueue.GENERAL, lambda *args: False, lambda *args: None, scope=scope
        )
    }
    for _ in range(80):
        queued()
    stop, seen, loops = Event(), [], [[]]
    locks = counted_work_locks(monkeypatch)
    real = processes.scan_once

    def scan(guard, **kwargs):
        """Record each scan's lock takes."""
        before = len(locks)
        result = real(guard, **kwargs)
        loops[-1].append(len(locks) - before)
        return result

    def heartbeat():
        """End after two loops, without real scheduler sleeps."""
        if len(loops) == 2:
            stop.set()
        else:
            loops.append([])

    monkeypatch.setattr(processes, "scan_once", scan)
    monkeypatch.setattr(
        processes, "publish_hint", lambda runtime, hint: seen.append(hint.run_id)
    )
    monkeypatch.setattr(stop, "wait", lambda timeout: stop.is_set())
    processes.serve_scheduler(
        BrokerRuntime(Mock(), ServiceRole.SCHEDULER),
        handlers=registry,
        lease=Mock(),
        stop=stop,
        heartbeat=heartbeat,
        produce=lambda guard: None,
    )
    cap = processes.EARLY_SCAN_ROWS
    assert loops == [[cap, 80 - cap], [cap, 80 - cap]] and not seen


def test_lost_ownership_in_the_early_scan_skips_the_producers(monkeypatch):
    """A lost scheduler session stops the loop before any producer runs."""
    produced, failures = [], []

    def scan(guard, **kwargs):
        raise SchedulerOwnershipLost("synthetic loss")

    monkeypatch.setattr(processes, "scan_once", scan)
    monkeypatch.setattr(processes, "emit_failure", failures.append)
    runtime = BrokerRuntime(Mock(), ServiceRole.SCHEDULER)
    with pytest.raises(SchedulerOwnershipLost):
        processes.serve_scheduler(
            runtime,
            handlers=handlers(),
            lease=Mock(),
            stop=Event(),
            heartbeat=Mock(),
            produce=produced.append,
        )
    assert not produced and not failures
    runtime.app.close.assert_called_once()


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


@pytest.mark.parametrize("checkpoint_fails", [False, True])
def test_loop_failure_retains_fair_cursor_and_discards_suffix_proof(
    monkeypatch, checkpoint_fails
):
    """Exercise real loop recovery after a partial page, including observer failure."""
    from parishkit.stewardship.jobs.due_work_health import DueWorkScan
    from parishkit.stewardship.jobs.due_work_models import DueWorkHealth

    queued()
    stop, cursors, seen, suffix_proof = Event(), [], [], []
    original_interrupted = DueWorkScan.interrupted

    def interrupted(health, guard):
        """The loop must discard proof even when durable invalidation is unavailable."""
        if checkpoint_fails:
            raise RuntimeError("synthetic checkpoint failure")
        original_interrupted(health, guard)

    def bounded_scan(guard, **kwargs):
        """Complete a prefix, fail its suffix once, then resume the same cursor."""
        cursors.append(kwargs["cursor"])
        if len(cursors) == 2:
            raise RuntimeError("synthetic suffix failure")
        if len(cursors) == 3:
            suffix_proof.append(kwargs["health"].started_at)
        result = scan_once(guard, **(kwargs | {"limit": 1}))
        seen.append(result.cursor)
        if len(cursors) == 3:
            # Scans 1 and 2 were loop 1's early and late scans (#394). Loop 2
            # is mid-sweep, so it has no early scan: scan 3 is its late
            # scan, which retries the failed suffix.
            stop.set()
        return result

    def heartbeat():
        """No real scheduler sleeps; the retried suffix ends the loop."""

    monkeypatch.setattr(processes, "scan_once", bounded_scan)
    monkeypatch.setattr(processes, "publish_hint", lambda *args: None)
    monkeypatch.setattr(DueWorkScan, "interrupted", interrupted)
    monkeypatch.setattr(stop, "wait", lambda timeout: stop.is_set())
    runtime = BrokerRuntime(Mock(), ServiceRole.SCHEDULER)
    assert (
        processes.serve_scheduler(
            runtime,
            handlers=handlers(),
            lease=Mock(),
            stop=stop,
            heartbeat=heartbeat,
            produce=lambda guard: None,
        )
        == 0
    )
    assert cursors[0] is None and cursors[1] is not None
    assert suffix_proof == [None]
    assert cursors[1] == cursors[2] == seen[0]
    assert seen[1] is None
    if checkpoint_fails:
        assert not DueWorkHealth.objects.exists()
    else:
        assert DueWorkHealth.objects.get().signal == "unknown"
