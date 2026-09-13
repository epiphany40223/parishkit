"""One durable final load per prepared setup, not a second catalog-load attempt.

The scheduler produces this metadata for the compiled finalization consumer,
which owns the atomic source/Family/configuration completion transaction.
"""

from functools import partial

from parishkit.stewardship.campaigns.work_locks import require_work_order
from parishkit.stewardship.jobs.dispatch import RecoveryPlan
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import TaskStatus, enqueue

from .models import SourceMutationLease
from .outcomes import MAX_AUTOMATIC_ATTEMPTS, retry_delay
from .setup_admission import source_available
from .setup_final_scope import require_prepared_setup

TASK_TYPE = "setup_finalize"


def bound_preparation(status):
    """A matching UUID alone cannot reinterpret a catalog or unrelated retry root."""
    require_work_order()
    if not isinstance(status, TaskStatus) or status.task_type != TASK_TYPE:
        raise PermissionError("Setup finalization task is unavailable.")
    task = TaskRun.objects.select_related("root").filter(pk=status.run_id).first()
    if (
        task is None
        or task.root_id != status.root_id
        or task.domain_request_id != status.domain_request_id
        or task.task_type != TASK_TYPE
        or (task.version, task.state, task.fence, task.worker_id)
        != (status.version, status.state, status.fence, status.worker_id)
        or task.root.idempotency_key != str(task.domain_request_id)
        or task.root.domain_request_id != task.domain_request_id
        or task.root.task_type != TASK_TYPE
        or task.initiated_by_id != task.root.initiated_by_id
    ):
        raise PermissionError("Setup finalization task binding differs.")
    return task


def require_final_task(status, *, store):
    """Require both current task metadata and its original prepared Admin intent."""
    task = bound_preparation(status)
    scope = require_prepared_setup(store, task.domain_request_id)
    if task.initiated_by_id != scope.owner_id:
        raise PermissionError("Setup finalization belongs to another initiator.")
    return scope


def enqueue_finalization(store, preparation_id, *, correlation_id):
    """Repeated scheduler passes retain one root without manufacturing success."""
    scope = require_prepared_setup(store, preparation_id)

    def admit(action, status):
        """Enqueue admission also runs before the new Task exists in SQL."""
        current = require_prepared_setup(store, preparation_id)
        return (
            action == "enqueue"
            and current == scope
            and status.task_type == TASK_TYPE
            and status.domain_request_id == preparation_id
        )

    return enqueue(
        task_type=TASK_TYPE,
        domain_request_id=preparation_id,
        actor_id=scope.owner_id,
        correlation_id=correlation_id,
        idempotency_key=preparation_id,
        admit=admit,
    )


def recovery_plan(status, *, store):
    """Only drained, still-original input may retry an abandoned read."""
    bound_preparation(status)
    if status.state != "abandoned":
        raise PermissionError("Setup finalization recovery requires abandonment.")
    if not source_available():
        return None
    try:
        require_final_task(status, store=store)
    except (PermissionError, LookupError):
        return RecoveryPlan("recovery_cancel")
    if status.attempt >= MAX_AUTOMATIC_ATTEMPTS:
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan("recovery_retry", retry_seconds=retry_delay(status.attempt))


def admit_finalization_task(action, status, *, store):
    """Only the atomic marker admits completion after pre-activation scope closes."""
    bound_preparation(status)
    if action == "complete":
        from parishkit.stewardship.accounts.setup_install_models import SetupCompletion

        return (
            SetupCompletion.objects.filter(
                preparation_id=status.domain_request_id,
                task_id=status.run_id,
                task_fence=status.fence,
                preparation__readiness__intent__attempt__state="completed",
            ).exists()
            and SourceMutationLease.objects.get(singleton=True).owner_id is None
        )
    if action == "lease_expired" or (
        action == "recovery_hint" and status.state == "running"
    ):
        return True
    if action in {
        "recovery_hint",
        "recovery_retry",
        "recovery_cancel",
        "recovery_fail",
    }:
        plan = recovery_plan(status, store=store)
        return plan is not None and (action == "recovery_hint" or action == plan.action)
    if action in {"retryable_failure", "permanent_failure", "safe_cancel"}:
        return SourceMutationLease.objects.get(singleton=True).owner_id != status.run_id
    require_final_task(status, store=store)
    if action in {"claim", "hint"}:
        return source_available()
    return action in {"heartbeat", "progress", "effect"}


def finalization_admission(store):
    """Bind the startup-owned public store, never a queue-supplied path."""
    return partial(admit_finalization_task, store=store)
