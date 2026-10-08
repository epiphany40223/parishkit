"""Process-local stop/renewal coordination rejects unsafe scopes before SQL."""

from dataclasses import replace
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest

from parishkit.stewardship.jobs.dispatch import Execution, Handler, WorkQueue
from parishkit.stewardship.jobs.lifetime import (
    RENEWAL_DRAIN_SECONDS,
    ExecutionInterrupted,
    RenewalDrainFailure,
    maintain_execution,
    renew_once,
)
from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.source.leases import SourceClaim


def execution():
    """Construct an unclaimed synthetic context; these tests never authorize SQL."""
    return Execution(
        TaskClaim(uuid4(), 1, uuid4()),
        Handler(WorkQueue.GENERAL, lambda *args: False, lambda *args: None),
        uuid4(),
    )


@pytest.mark.parametrize("signal", ["stop", "failed", "finished"])
def test_new_unit_is_denied_after_stop_loss_or_completion(signal):
    """Signals cannot be confused with durable successful completion."""
    context = execution()
    getattr(context.control, signal).set()
    with pytest.raises(ExecutionInterrupted):
        context.check()


@pytest.mark.parametrize("signal", ["failed", "finished"])
def test_inflight_drain_cannot_ignore_lost_ownership_or_completion(signal):
    """Only graceful stop is drainable; a failed/finished owner cannot query SQL."""
    context = execution()
    context.control.stop.set()
    getattr(context.control, signal).set()
    with pytest.raises(ExecutionInterrupted):
        context.check_inflight()


def test_completed_context_does_not_attempt_a_new_heartbeat():
    """A completion racing the timer never tries to renew a terminal TaskRun."""
    context = execution()
    context.control.finished.set()
    renew_once(context)


def test_lifetime_stops_on_exception_and_cannot_be_reentered():
    """One claim has exactly one process-local lifetime, even after an error."""
    context = execution()
    with pytest.raises(RuntimeError, match="synthetic"), maintain_execution(context):
        assert context.control.active
        raise RuntimeError("synthetic")
    assert not context.control.active
    with pytest.raises(ExecutionInterrupted), maintain_execution(context):
        pytest.fail("Lifetime was reused")


def test_nested_lifetime_is_denied():
    """A second renewer cannot change the first lifetime's source attachment."""
    context = execution()
    with (
        maintain_execution(context),
        pytest.raises(ExecutionInterrupted),
        maintain_execution(context),
    ):
        pytest.fail("Nested lifetime was admitted")


@pytest.mark.parametrize("stop", [True, "stop", object()])
def test_lifetime_rejects_non_event_drainage(stop):
    """No loose flag or caller string substitutes for the process-owned event."""
    with pytest.raises(ValueError), maintain_execution(execution(), stop=stop):
        pytest.fail("Invalid stop event was admitted")


def test_already_stopping_lifetime_never_starts_renewal():
    """A signal between claim and lifetime entry leaves ordinary expiry recovery."""
    context, stop = execution(), Event()
    stop.set()
    with pytest.raises(ExecutionInterrupted), maintain_execution(context, stop=stop):
        pytest.fail("Stopped lifetime was admitted")
    assert not context.control.active


def test_source_attachment_requires_exact_owner_and_active_nonnested_lifetime():
    """An arbitrary, stale or other-task source lease cannot join this renewer."""
    context = execution()
    claim = SourceClaim(
        context.claim.run_id, context.claim.fence, context.claim.worker_id, 1, "full"
    )
    with pytest.raises(ValueError), context.maintain_source(object()):
        pytest.fail("Invalid source claim was attached")
    with pytest.raises(ExecutionInterrupted), context.maintain_source(claim):
        pytest.fail("Inactive source claim was attached")
    with maintain_execution(context), context.maintain_source(claim):
        assert context.control.source_claim is claim
        with pytest.raises(ExecutionInterrupted), context.maintain_source(claim):
            pytest.fail("Nested source claim was attached")
        different = SourceClaim(uuid4(), 1, uuid4(), 1, "full")
        with pytest.raises(ValueError), context.maintain_source(different):
            pytest.fail("Another worker's source claim was attached")
    assert context.control.source_claim is None


@pytest.mark.parametrize("body_fails", [False, True])
def test_undrained_renewer_is_fatal_even_when_handler_already_failed(
    monkeypatch, body_fails
):
    """Ordinary broker error handling must not resume beside an old live renewer:
    the hint task stops the consumer (Celery's stop flag 70, #386 M5)."""
    from parishkit.stewardship.deployment import ServiceRole, ValkeyConfiguration
    from parishkit.stewardship.jobs import broker, lifetime

    context = execution()
    thread = Mock(ident=123)
    thread.is_alive.return_value = True
    monkeypatch.setattr(lifetime, "Thread", Mock(return_value=thread))

    def consume(*args, **kwargs):
        """Exercise the real lifetime exit through the registered broker task."""
        with maintain_execution(context):
            if body_fails:
                raise ValueError("synthetic-handler-failure")

    from threading import Event as ThreadEvent

    from celery.worker import state

    monkeypatch.setattr(broker, "consume_hint", consume)
    monkeypatch.setattr(state, "should_stop", None)
    monkeypatch.setattr(broker, "_fatal", ThreadEvent())
    errors = []
    original = broker.stop_consumer
    monkeypatch.setattr(
        broker,
        "stop_consumer",
        lambda error, args, stop: (errors.append(error), original(error, args, stop)),
    )
    runtime = broker.build_broker(
        endpoint=ValkeyConfiguration("valkey", 6379, 0, None),
        password="synthetic-password",
        service=ServiceRole.WORKER,
        handlers={},
    )
    runtime.app.tasks[broker.HINT_TASK].run(str(uuid4()))
    assert state.should_stop == broker.FATAL_EXIT_STATUS
    [error] = errors
    assert isinstance(error, RenewalDrainFailure)
    assert context.control.failed.is_set() and not context.control.active
    thread.join.assert_called_once_with(timeout=RENEWAL_DRAIN_SECONDS)
    if body_fails:
        assert isinstance(error.__context__, ValueError)


def test_inflight_check_skips_while_this_workers_lock_is_busy(monkeypatch):
    """A heartbeat holding the control lock cannot stall a helper's lease check.

    The tick is skipped (no SQL) instead of waiting past the helper's
    deadline (#318).
    """
    import threading
    import time

    from parishkit.stewardship.jobs import dispatch

    monkeypatch.setattr(dispatch, "INFLIGHT_LOCK_SECONDS", 0.1)
    reports = []
    monkeypatch.setattr(dispatch, "record_inflight_skip", reports.append)
    context = execution()
    held, done = threading.Event(), threading.Event()

    def hold():
        with context.control.lock:
            held.set()
            done.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        held.wait(5)
        started = time.monotonic()
        assert context.check_inflight() is False
        assert time.monotonic() - started < 1
        # A run of skips is reported once at its start ...
        assert context.check_inflight() is False
        assert len(reports) == 1
        assert reports[0]["what"] == "control_lock" and reports[0]["skipped"] == 1
        assert reports[0]["task_id"] == context.claim.run_id
        assert reports[0]["limit_seconds"] == 0.1
        assert 0.1 <= reports[0]["elapsed_seconds"] < 1
    finally:
        done.set()
        thread.join(5)
    # ... and summed up when it ends (here, at the next transition).
    context.skips.ended()
    assert len(reports) == 2 and reports[1]["skipped"] == 2
    assert reports[1]["elapsed_seconds"] >= 0.2


def test_inflight_check_refuses_to_run_inside_a_transaction(monkeypatch):
    """Its transaction-local limits must never leak into a caller's transaction."""
    from types import SimpleNamespace

    from parishkit.stewardship.jobs import dispatch
    from parishkit.stewardship.storage import StorageInvariantError

    monkeypatch.setattr(dispatch, "connection", SimpleNamespace(in_atomic_block=True))
    with pytest.raises(StorageInvariantError):
        execution().check_inflight()


@pytest.mark.parametrize("renewed", [True, False])
def test_the_renewal_loop_pulses_after_its_timing_and_outside_the_lock(
    monkeypatch, renewed
):
    """ADM-13: the pulse writes the status record, so it holds no control lock
    and is not counted in the renewal's wait_ms; no renewal, no pulse."""
    from threading import Event as ThreadEvent

    from parishkit.stewardship.jobs import lifetime

    context = execution()
    order, waits, held = [], [], []

    def renew(actual):
        """A renewal that succeeded, or found its task finished."""
        order.append("renew")
        return renewed

    def pulse():
        """Note whether the control lock was held; the loop would swallow an
        assertion raised here, so the test checks the flag afterwards."""
        free = context.control.lock.acquire(blocking=False)
        if free:
            context.control.lock.release()
        held.append(not free)
        order.append("pulse")

    monkeypatch.setattr(lifetime, "renew_once", renew)
    monkeypatch.setattr(lifetime, "_renewal_timing", lambda _: order.append("timed"))
    monkeypatch.setattr("django.db.connections.close_all", lambda: None)
    context = replace(context, handler=replace(context.handler, pulse=pulse))
    stopped = ThreadEvent()

    def one_pass(seconds):
        """Let exactly one pass run; any further wait stops the loop, so a
        regression ends the test instead of hanging it."""
        waits.append(seconds)
        return len(waits) > 1

    monkeypatch.setattr(stopped, "wait", one_pass)
    lifetime._renewal_loop(context, stopped)
    assert order == (["renew", "timed", "pulse"] if renewed else ["renew", "timed"])
    assert held == ([False] if renewed else [])
    assert len(waits) == 2
