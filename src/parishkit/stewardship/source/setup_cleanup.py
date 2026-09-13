"""Dispose unused terminal-setup staging, never its promoted source truth."""

from uuid import uuid4

from django.db import connection
from django.db.models import Exists, OuterRef

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now, lock_task_claim
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.jobs.storage import TaskStatus, _status, change_run, enqueue
from parishkit.stewardship.storage import StorageInvariantError

from .leases import acquire_source, release_source
from .setup_admission import admit_setup_task, source_available
from .setup_disposal import (
    TASK_TYPE,
    dispose_batch,
    owned_snapshots,
    owned_source_tasks,
    terminal_attempt,
)
from .version_models import ENTITY_MODELS


def _attempt(status, *, creating=False):
    """Queue IDs must match a terminal setup and exact Task status."""
    require_work_order()
    if not isinstance(status, TaskStatus) or status.task_type != TASK_TYPE:
        raise PermissionError("This Task cannot dispose setup source data.")
    if (
        not creating
        and not TaskRun.objects.filter(
            pk=status.run_id,
            root_id=status.root_id,
            task_type=TASK_TYPE,
            domain_request_id=status.domain_request_id,
            state=status.state,
            version=status.version,
            fence=status.fence,
            worker_id=status.worker_id,
        ).exists()
    ):
        raise PermissionError("Setup cleanup Task ownership differs.")
    return terminal_attempt(status.domain_request_id)


def pending(attempt_id):
    """Read concrete source membership only in the actual worker, never scheduler."""
    snapshots = owned_snapshots(attempt_id)
    return snapshots.exclude(state="rejected").exists() or any(
        membership.objects.filter(snapshot_id__in=snapshots.values("id")).exists()
        for _, membership in ENTITY_MODELS.values()
    )


def recover_cleanup(status):
    """Retry drained cleanup at most five times, retaining explicit failure."""
    _attempt(status)
    if (
        status.state != "abandoned"
        or not source_available()
        or not SystemConfiguration.objects.filter(
            restore_review_required=False
        ).exists()
    ):
        return None
    if status.attempt >= 5:
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan(
        "recovery_retry", retry_seconds=min(30 * 2 ** max(status.attempt - 1, 0), 600)
    )


def admit_cleanup(action, status):
    """Closed transitions never translate an empty snapshot count into success."""
    attempt = _attempt(status, creating=action == "enqueue")
    if action == "lease_expired" or (
        action == "recovery_hint" and status.state == "running"
    ):
        return True
    if action in {"recovery_hint", "recovery_retry", "recovery_fail"}:
        plan = recover_cleanup(status)
        return plan is not None and (action == "recovery_hint" or action == plan.action)
    if not SystemConfiguration.objects.filter(restore_review_required=False).exists():
        return False
    if action == "complete":
        return not pending(attempt.pk)
    if action in {"claim", "hint", "explicit_retry", "explicit_retry_replay"}:
        return source_available()
    return action in {"enqueue", "effect", "heartbeat", "progress"}


def produce_setup_cleanup(guard):
    """Queue one original terminal attempt per scheduler pass, idempotently."""
    if not isinstance(guard, SchedulerGuard):
        raise TypeError("Setup cleanup requires its actual scheduler guard.")
    if connection.in_atomic_block:
        raise StorageInvariantError("Setup cleanup production owns its transaction.")
    guard.check()
    with work_transaction():
        if not SystemConfiguration.objects.filter(
            restore_review_required=False
        ).exists():
            return ()
        scheduled = TaskRun.objects.filter(
            task_type=TASK_TYPE, domain_request_id=OuterRef("id")
        )
        attempt = (
            SetupAttempt.objects.filter(
                state__in=("expired", "completed"),
                source_task_id__isnull=False,
            )
            .filter(~Exists(scheduled))
            .order_by("created_at", "id")
            .first()
        )
        if attempt is None:
            return ()
        result = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=attempt.pk,
            actor_id=None,
            correlation_id=uuid4(),
            idempotency_key=attempt.pk,
            admit=admit_cleanup,
        )
    guard.check()
    return (result.run_id,)


def cleanup_handler(*, scheduler=False):
    """Only the worker registry binds deletion; scheduling contains metadata only."""

    def unavailable(execution):
        """A scheduler has neither source payload access nor executable deletion."""
        raise PermissionError("The scheduler cannot dispose source data.")

    return Handler(
        WorkQueue.GENERAL,
        admit_cleanup,
        unavailable if scheduler else _execute,
        recover=recover_cleanup,
        scope=work_transaction,
    )


def _settle_original(attempt_id, execution):
    """Cancel both original read chains; live workers retain normal lease expiry."""
    from .setup_final_tasks import finalization_admission

    for row in owned_source_tasks(attempt_id).order_by("created_at", "id"):
        # The terminal original attempt closes finalization before its verifier
        # reaches filesystem selection. No replacement authority can authorize
        # work here; only expiry and drained cancellation transitions are used.
        admit = (
            finalization_admission(None)
            if row.task_type == "setup_finalize"
            else admit_setup_task
        )
        if row.state == "running" and row.lease_expires_at <= database_now():
            change_run(
                run_id=row.pk,
                expected_version=row.version,
                action="lease_expired",
                actor_id=None,
                correlation_id=execution.correlation_id,
                admit=admit,
            )
            row.refresh_from_db()
        if row.state in {"queued", "retry_wait", "abandoned"}:
            change_run(
                run_id=row.pk,
                expected_version=row.version,
                action="recovery_cancel" if row.state == "abandoned" else "safe_cancel",
                actor_id=execution.claim.worker_id,
                correlation_id=execution.correlation_id,
                admit=admit,
            )


def _execute(execution):
    """Retire drained manifests and delete short batches under both renewed fences."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Source disposal requires maintained execution.")
    with execution.effect():
        claim = acquire_source(
            task_id=execution.claim.run_id,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            phase="full",
        )
    done = 0
    with execution.maintain_source(claim):
        while True:
            with execution.effect():
                count = dispose_batch(execution, claim)
            if count is None:
                break
            done += count
            execution.progress(done, done, phase=TaskPhase.DELETING)
    with execution.control.lock:
        with work_transaction():
            status = _status(lock_task_claim(execution.claim))
            release_source(claim)
            _settle_original(status.domain_request_id, execution)
            change_run(
                run_id=status.run_id,
                expected_version=status.version,
                action="complete",
                actor_id=execution.claim.worker_id,
                correlation_id=execution.correlation_id,
                fence=execution.claim.fence,
                admit=admit_cleanup,
            )
        execution.control.finished.set()
