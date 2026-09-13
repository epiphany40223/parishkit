"""Renewal uses real independent SQL sessions and cannot manufacture outcomes."""

from threading import Event, get_ident
from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.jobs import lifetime
from parishkit.stewardship.jobs.dispatch import Handler, WorkQueue, execute_hint
from parishkit.stewardship.jobs.lifetime import ExecutionInterrupted
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source.leases import (
    SourceFenceLost,
    acquire_source,
    release_source,
)
from parishkit.stewardship.source.models import SourceMutationLease

from .test_dispatch_postgresql import queued

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def idle_source_lease():
    """Recreate the migration seed after transaction-test flush removes it."""
    SourceMutationLease.objects.get_or_create(singleton=True)


def run(task, execute, *, admit=None, stop=None):
    """Dispatch a synthetic bounded handler with the actual heartbeat thread."""
    return execute_hint(
        task.run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={
            "dispatch_probe": Handler(
                WorkQueue.GENERAL, admit or (lambda *args: True), execute
            )
        },
        stop=stop,
    )


def pulse_notice(monkeypatch):
    """Signal after a genuine committed renewal, without twenty-second tests."""
    notice, threads = Event(), []
    original = lifetime.renew_once

    def renew(execution):
        """Keep the production renewal, only observe its successful completion."""
        original(execution)
        threads.append(get_ident())
        notice.set()

    monkeypatch.setattr(lifetime, "PULSE_SECONDS", 0.02)
    monkeypatch.setattr(lifetime, "renew_once", renew)
    return notice, threads


def test_heartbeat_runs_outside_work_connection_and_extends_task(monkeypatch):
    """A slow handler owns no transaction while the separate renewal commits."""
    task = queued()
    notice, threads = pulse_notice(monkeypatch)
    settings = connection.settings_dict

    def execute(context):
        """Wait for a heartbeat, then verify progress and explicit completion."""
        with context.control.lock:
            before = TaskRun.objects.get(pk=task.run_id).lease_expires_at
            notice.clear()
        assert not connection.in_atomic_block and notice.wait(5)
        assert TaskRun.objects.get(pk=task.run_id).lease_expires_at > before
        context.check()
        context.transition("complete")

    assert run(task, execute)
    assert threads and all(thread != get_ident() for thread in threads)
    assert connection.settings_dict is settings
    assert TaskRun.objects.get(pk=task.run_id).state == "succeeded"


def test_source_claim_renews_with_task_then_detaches_before_release(monkeypatch):
    """A corpus load may span many pulses without surrendering source ownership."""
    task = queued()
    notice, _ = pulse_notice(monkeypatch)

    def execute(context):
        """Attach only the exact owned lease around the simulated provider call."""
        claim = acquire_source(
            task_id=context.claim.run_id,
            task_fence=context.claim.fence,
            worker_id=context.claim.worker_id,
            phase="full",
        )
        before = SourceMutationLease.objects.get().expires_at
        with context.maintain_source(claim):
            notice.clear()
            assert notice.wait(5)
            assert SourceMutationLease.objects.get().expires_at > before
        release_source(claim)
        notice.clear()
        assert notice.wait(5)
        assert SourceMutationLease.objects.get().owner_id is None
        context.transition("complete")

    assert run(task, execute)
    assert TaskRun.objects.get(pk=task.run_id).state == "succeeded"


def test_revoked_renewal_stops_new_work_and_never_fakes_terminal_state(monkeypatch):
    """A domain gate change fails closed even before the old lease expires."""
    task = queued()
    monkeypatch.setattr(lifetime, "PULSE_SECONDS", 0.02)

    def admit(action, status):
        """Model a fresh gate denying renewal after an initially valid claim."""
        return action == "claim"

    def execute(context):
        """Wait for genuine admission failure, then reject every further effect."""
        assert context.control.failed.wait(5)
        with pytest.raises(ExecutionInterrupted):
            context.check()
        with pytest.raises(ExecutionInterrupted):
            context.transition("complete")

    assert run(task, execute, admit=admit)
    assert TaskRun.objects.get(pk=task.run_id).state == "running"


def test_task_and_source_renewal_roll_back_together_on_source_fence_loss(monkeypatch):
    """A released source lease cannot be covered up by a successful task pulse."""
    task = queued()
    monkeypatch.setattr(lifetime, "PULSE_SECONDS", 0.05)

    def execute(context):
        """Simulate loss after attachment; neither independent lease is revived."""
        claim = acquire_source(
            task_id=context.claim.run_id,
            task_fence=context.claim.fence,
            worker_id=context.claim.worker_id,
            phase="full",
        )
        with context.control.lock, context.maintain_source(claim):
            release_source(claim)
            before = TaskRun.objects.get(pk=task.run_id).lease_expires_at
            with pytest.raises(SourceFenceLost):
                lifetime.renew_once(context)
            assert TaskRun.objects.get(pk=task.run_id).lease_expires_at == before

    assert run(task, execute)


def test_stop_before_claim_leaves_queued_work_available_to_next_worker():
    """Drainage does not claim a new unit just to strand it."""
    task, stop = queued(), Event()
    stop.set()
    assert not run(task, lambda context: pytest.fail("Unexpected execution"), stop=stop)
    assert TaskRun.objects.get(pk=task.run_id).state == "queued"


def test_stop_during_unit_allows_verified_completion_but_not_new_external_work():
    """Current safe effects can commit; a stop signal does not mean cancellation."""
    task, stop = queued(), Event()

    def execute(context):
        """Model SIGTERM while the handler finishes its current bounded unit."""
        stop.set()
        with pytest.raises(ExecutionInterrupted):
            context.check()
        context.transition("complete")

    assert run(task, execute, stop=stop)
    assert TaskRun.objects.get(pk=task.run_id).state == "succeeded"


def test_exception_stops_background_renewal_and_preserves_claim(monkeypatch):
    """The daemon never survives a handler scope continuing to renew dead work."""
    task = queued()
    notice, _ = pulse_notice(monkeypatch)
    controls = []

    def execute(context):
        """Fail after renewal has actually used its independent connection."""
        controls.append(context.control)
        assert notice.wait(5)
        raise RuntimeError("synthetic interruption")

    with pytest.raises(RuntimeError, match="synthetic interruption"):
        run(task, execute)
    assert not controls[0].active
    assert TaskRun.objects.get(pk=task.run_id).state == "running"
