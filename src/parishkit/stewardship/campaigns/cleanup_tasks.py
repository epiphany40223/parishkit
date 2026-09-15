"""Compiled general-worker owner for exact Production-transition cleanup."""

from uuid import uuid4

from django.db import DatabaseError, IntegrityError, connection

from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import TaskStatus
from parishkit.stewardship.jobs.storage import _status as task_status
from parishkit.stewardship.storage import StorageInvariantError

from .cleanup_batches import apply_checkpoint
from .credential_models import CampaignCredentialState
from .models import CampaignWorkGate
from .production_models import (
    ProductionCleanupCancellation,
    ProductionCleanupManifest,
    ProductionTransitionRequest,
)
from .production_states import ProductionAction as Action
from .production_storage import change_transition
from .work_locks import require_work_order, work_transaction

TASK_TYPE = "production_cleanup"


def cancellation_requested(request):
    """Read only durable, Admin-owned intent; a queue hint cannot request a stop."""
    return ProductionCleanupCancellation.objects.filter(request=request).exists()


def owned_request(status):
    """Only exact durable task metadata and a verified manifest identify work."""
    require_work_order()
    if (
        not isinstance(status, TaskStatus)
        or status.task_type != TASK_TYPE
        or not TaskRun.objects.filter(
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
        raise PermissionError("Cleanup task ownership is unavailable.")
    request = ProductionTransitionRequest.objects.filter(
        pk=status.domain_request_id, task_id=status.root_id
    ).first()
    if (
        request is None
        or not ProductionCleanupManifest.objects.filter(request=request).exists()
    ):
        raise PermissionError("Cleanup requires its sealed request binding.")
    return request


def eligible(request):
    """Cleanup alone may own the go-live gate, never a restore or purge exception."""
    scope = _scope(request.campaign_id)
    return (
        scope.runtime.current_campaign_id == request.campaign_id
        and scope.runtime.active_configuration_id == request.configuration_id
        and scope.runtime.mode == "testing"
        and not scope.runtime.restore_review_required
        and scope.campaign.state == "draft"
        and not CampaignWorkGate.objects.filter(
            state__in=("preparing", "running")
        ).exists()
        and CampaignCredentialState.objects.filter(
            campaign_id=request.campaign_id,
            go_live_gate=True,
            rehearsal_epoch_id__isnull=True,
            version__gte=request.gate_version,
        ).exists()
    )


def recover_cleanup(status):
    """Use committed outcomes; interrupted batches have no committed effects."""
    request = owned_request(status)
    if status.state != "abandoned":
        return None
    if cancellation_requested(request):
        return RecoveryPlan("recovery_cancel")
    if request.state == "cleanup_complete":
        return RecoveryPlan("recovery_complete")
    if request.state == "cancelled":
        return RecoveryPlan("recovery_cancel")
    if not eligible(request):
        return None
    if request.state == "cleanup_failed" or status.attempt >= 5:
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan(
        "recovery_retry", min(30 * 2 ** max(status.attempt - 1, 0), 600)
    )


def admit_cleanup(action, status):
    """Every task command rechecks immutable ownership and current domain gates."""
    request = owned_request(status)
    if action in {"lease_expired", "recovery_hint"}:
        return True
    if action == "complete":
        return request.state == "cleanup_complete" and not cancellation_requested(
            request
        )
    if action == "safe_cancel":
        return request.state == "cancelled" or cancellation_requested(request)
    if action in {
        "recovery_complete",
        "recovery_cancel",
        "recovery_retry",
        "recovery_fail",
    }:
        plan = recover_cleanup(status)
        return plan is not None and plan.action == action
    if (
        action in {"hint", "claim", "effect", "heartbeat", "progress"}
        and request.state == "cleanup_complete"
    ):
        return True
    if not eligible(request):
        return False
    if action == "permanent_failure":
        return request.state == "cleanup_failed"
    if action == "retryable_failure":
        return request.state == "cleanup_retry_wait"
    return action in {
        "hint",
        "claim",
        "effect",
        "heartbeat",
        "progress",
    } and request.state in {"cleanup_queued", "cleanup_running", "cleanup_retry_wait"}


def _change(execution, request, action, *, reason=""):
    """Bind a domain transition to the maintained worker and its current request."""
    binding = {}
    if action in {Action.START, Action.RECOVER}:
        binding = dict(run_id=execution.claim.run_id, task_fence=execution.claim.fence)

    def admit(kind, campaign, status, proposal):
        """Never pass caller-owned callbacks into a runtime domain transition."""
        execution.check()
        current = owned_request(task_status(lock_task_claim(execution.claim)))
        return (
            kind is action
            and proposal.action is action
            and campaign.pk == request.campaign_id
            and status.request_id == request.pk
            and current.pk == request.pk
            and eligible(current)
        )

    return change_transition(
        request_id=request.pk,
        action=action,
        command_id=uuid4(),
        expected_version=request.version,
        actor_id=execution.claim.worker_id,
        correlation_id=execution.correlation_id,
        admit=admit,
        failure_reason=reason,
        **binding,
    )


def _execute(execution):
    """Execute only bounded local transactions; acknowledge durable completion last."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Cleanup requires a maintained worker lifetime.")
    try:
        with execution.effect():
            request = owned_request(task_status(lock_task_claim(execution.claim)))
            if cancellation_requested(request):
                execution.transition("safe_cancel")
                return
            if request.state != "cleanup_complete":
                action = (
                    Action.RECOVER
                    if request.state == "cleanup_running"
                    else Action.START
                )
                _change(execution, request, action)
        while True:
            with execution.effect():
                request = owned_request(task_status(lock_task_claim(execution.claim)))
                if cancellation_requested(request):
                    execution.transition("safe_cancel")
                    return
                if request.state == "cleanup_complete":
                    break
                if request.processed_count == request.inventory_total:
                    _change(execution, request, Action.COMPLETE)
                    break
                # Renew after lock waits and fresh admission, giving this bounded
                # local batch a full lease budget. SQL still rejects actual expiry
                # during deletion; this cannot revive an already-expired claim.
                execution.heartbeat(seconds=60)
                progress = apply_checkpoint(request.pk, execution.claim)
            execution.progress(progress.processed_count, progress.inventory_total)
    except IntegrityError:
        # Constraint denials are not transient database failures. Preserve their
        # original fencing failure; abandoned claims use ordinary recovery.
        raise
    except DatabaseError:
        # A failed batch rolled back. Only a still-current claim can journal a
        # sanitized retry/failure; permission and lost-ownership errors propagate.
        with execution.effect():
            task = lock_task_claim(execution.claim)
            request = owned_request(task_status(task))
            if cancellation_requested(request):
                execution.transition("safe_cancel")
                return
            if (
                request.state != "cleanup_running"
                or request.run_id != task.pk
                or request.task_fence != task.fence
            ):
                # A failed initial START/RECOVER transaction has no committed
                # domain binding. Establish it under the still-current claim
                # before recording the sanitized retry or exhaustion outcome.
                _change(
                    execution,
                    request,
                    Action.RECOVER
                    if request.state == "cleanup_running"
                    else Action.START,
                )
                request.refresh_from_db()
            exhausted = task.attempt >= 5
            _change(
                execution,
                request,
                Action.FAIL if exhausted else Action.RETRY_LATER,
                reason="cleanup_exhausted" if exhausted else "cleanup_database_failure",
            )
            execution.transition(
                "permanent_failure" if exhausted else "retryable_failure",
                **(
                    {}
                    if exhausted
                    else {"retry_seconds": min(30 * 2 ** max(task.attempt - 1, 0), 600)}
                ),
            )
        return
    # The Admin may have requested cancellation after domain completion. Check
    # and acknowledge in one work transaction so neither outcome strands intent.
    with execution.effect():
        request = owned_request(task_status(lock_task_claim(execution.claim)))
        execution.transition(
            "safe_cancel" if cancellation_requested(request) else "complete"
        )


def cleanup_handler(*, scheduler=False):
    """Scheduler has metadata recovery only, never an executable deletion port."""
    if type(scheduler) is not bool:
        raise TypeError("Cleanup requires a compiled service role.")

    def unavailable(execution):
        """Do not let a scheduler registry value execute data deletion."""
        raise PermissionError("The scheduler cannot execute Production cleanup.")

    return Handler(
        WorkQueue.GENERAL,
        admit_cleanup,
        unavailable if scheduler else _execute,
        recover=recover_cleanup,
        scope=work_transaction,
    )
