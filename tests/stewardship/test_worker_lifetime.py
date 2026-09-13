"""Process-local stop/renewal coordination rejects unsafe scopes before SQL."""

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
    """Ordinary broker error handling must not resume beside an old live renewer."""
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

    monkeypatch.setattr(broker, "consume_hint", consume)
    runtime = broker.build_broker(
        endpoint=ValkeyConfiguration("valkey", 6379, 0, None),
        password="synthetic-password",
        service=ServiceRole.WORKER,
        handlers={},
    )
    with pytest.raises(RenewalDrainFailure) as caught:
        runtime.app.tasks[broker.HINT_TASK].run(str(uuid4()))
    assert context.control.failed.is_set() and not context.control.active
    thread.join.assert_called_once_with(timeout=RENEWAL_DRAIN_SECONDS)
    if body_fails:
        assert isinstance(caught.value.__context__, ValueError)
