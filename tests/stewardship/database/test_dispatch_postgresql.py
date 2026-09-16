"""Duplicate broker delivery cannot replace durable ownership or domain admission."""

from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.jobs.dispatch import (
    Handler,
    RecoveryPlan,
    WorkQueue,
    execute_hint,
    recover_hint,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import change_run, enqueue

pytestmark = pytest.mark.django_db(transaction=True)


def queued():
    """Create one explicitly authorized synthetic domain operation."""
    return enqueue(
        task_type="dispatch_probe",
        domain_request_id=uuid4(),
        actor_id=None,
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )


def test_duplicate_hint_executes_once_outside_database_transaction():
    """The durable row, not the broker's message count, determines execution."""
    task, worker, calls = queued(), uuid4(), []

    def execute(context):
        """Synthetic bounded work with durable progress and verified completion."""
        assert not connection.in_atomic_block
        calls.append(context.claim)
        context.heartbeat()
        context.progress(1, 1)
        context.transition("complete")

    handlers = {
        "dispatch_probe": Handler(WorkQueue.GENERAL, lambda *args: True, execute)
    }
    arguments = dict(queue=WorkQueue.GENERAL, worker_id=worker, handlers=handlers)
    assert execute_hint(task.run_id, **arguments)
    assert not execute_hint(task.run_id, **arguments)
    assert len(calls) == 1 and TaskRun.objects.get(pk=task.run_id).state == "succeeded"


def test_queue_and_domain_admission_are_both_required():
    """A correct routing string never bypasses transactional domain authorization."""
    task, called = queued(), []
    handler = Handler(WorkQueue.GENERAL, lambda *args: False, called.append)
    for queue in (WorkQueue.GENERAL, WorkQueue.MAIL):
        with pytest.raises(PermissionError):
            execute_hint(
                task.run_id,
                queue=queue,
                worker_id=uuid4(),
                handlers={"dispatch_probe": handler},
            )
    assert not called and TaskRun.objects.get(pk=task.run_id).state == "queued"


def test_unexpected_worker_failure_preserves_unresolved_claim():
    """An exception says nothing about whether external effects were accepted."""
    task = queued()

    def fail(context):
        """Simulate interruption after recording resumable progress."""
        context.progress(1, 2)
        raise RuntimeError("synthetic interrupted external work")

    with pytest.raises(RuntimeError, match="synthetic interrupted"):
        execute_hint(
            task.run_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={
                "dispatch_probe": Handler(WorkQueue.GENERAL, lambda *args: True, fail)
            },
        )
    row = TaskRun.objects.get(pk=task.run_id)
    assert row.state == "running" and row.progress_current == 1


def test_revoked_completion_admission_never_marks_success():
    """Admission is repeated at the last durable effect, not cached at claim."""
    task = queued()

    def admit(action, status):
        """Synthetic domain evidence admits the claim, but not completion."""
        return action == "claim"

    handler = Handler(
        WorkQueue.GENERAL, admit, lambda context: context.transition("complete")
    )
    with pytest.raises(PermissionError):
        execute_hint(
            task.run_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"dispatch_probe": handler},
        )
    assert TaskRun.objects.get(pk=task.run_id).state == "running"


def expired_task():
    """Use a real brief database lease, without disabling ownership guards."""
    task = queued()
    change_run(
        run_id=task.run_id,
        expected_version=task.version,
        action="claim",
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: True,
        lease_seconds=1,
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(1.05)")
    return task


def test_uncertain_recovery_fences_owner_but_preserves_unresolved_work():
    """Lease expiry proves ownership loss, not successful cancellation or failure."""
    task = expired_task()
    handler = Handler(WorkQueue.GENERAL, lambda *args: True, lambda context: None)
    assert not recover_hint(
        task.run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={"dispatch_probe": handler},
    )
    row = TaskRun.objects.get(pk=task.run_id)
    assert row.state == "abandoned" and row.fence == 2


def test_verified_recovery_preserves_retry_delay_and_execution_identity():
    """A safe retry resumes the same nonterminal operation, not a duplicate root."""
    task = expired_task()
    handler = Handler(
        WorkQueue.GENERAL,
        lambda *args: True,
        lambda context: None,
        recover=lambda status: RecoveryPlan("recovery_retry", 30),
    )
    arguments = dict(
        queue=WorkQueue.GENERAL, worker_id=uuid4(), handlers={"dispatch_probe": handler}
    )
    assert recover_hint(task.run_id, **arguments)
    assert TaskRun.objects.get(pk=task.run_id).state == "retry_wait"
    assert not execute_hint(task.run_id, **arguments)
    assert TaskRun.objects.count() == 1


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("fail_callback", [False, True])
def test_post_transition_hook_is_atomic_and_not_replayed(recovery, fail_callback):
    """Callbacks observe the written state; failure rolls back state and diagnostics."""
    from parishkit.stewardship.audit.models import OperationalLog
    from parishkit.stewardship.audit.schemas import ContextKind
    from parishkit.stewardship.audit.services import operational
    from parishkit.stewardship.observability import Event

    task = expired_task() if recovery else queued()
    calls = []

    def after(action, status):
        """Only an actual journal transition may create its matching diagnostic."""
        assert connection.in_atomic_block
        assert (
            status.state == TaskRun.objects.get(pk=status.run_id).state == "succeeded"
        )
        calls.append(action)
        operational(
            Event.TASK_COMPLETED,
            schema=ContextKind.TASK,
            context={"task_id": status.run_id},
        )
        if fail_callback:
            raise RuntimeError("synthetic post-transition fault")

    handler = Handler(
        WorkQueue.GENERAL,
        lambda *args: True,
        lambda execution: execution.transition("complete"),
        recover=lambda status: RecoveryPlan("recovery_complete"),
        after_transition=after,
    )
    invoke = recover_hint if recovery else execute_hint
    options = dict(
        queue=WorkQueue.GENERAL, worker_id=uuid4(), handlers={"dispatch_probe": handler}
    )
    if fail_callback:
        with pytest.raises(RuntimeError, match="post-transition fault"):
            invoke(task.run_id, **options)
        assert TaskRun.objects.get(pk=task.run_id).state == "running"
        assert not OperationalLog.objects.exists()
    else:
        assert invoke(task.run_id, **options)
        assert not invoke(task.run_id, **options)
        assert OperationalLog.objects.count() == 1
    assert calls == ["recovery_complete" if recovery else "complete"]
