"""Compiled boundary task ownership, ordered execution and evidence-based recovery.

Each hint identifies one occurrence through its immutable root execution key.
The lifecycle executor may apply its due predecessor/successor in the same short
transaction. A different hint can subsequently acknowledge that same terminal
occurrence without applying a second transition or reopening failed task history.
"""

from uuid import UUID

from django.db import connection

from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import TaskStatus, _status
from parishkit.stewardship.storage import StorageInvariantError

from .boundaries import apply_due_boundaries
from .boundary_production import TASK_TYPE, boundary_scope
from .lifecycle import Action
from .models import CampaignBoundaryOccurrence
from .work_locks import require_work_order, work_transaction


def _occurrence(status):
    """Bind an exact current task view to its original campaign and occurrence."""
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
        raise PermissionError("Boundary task ownership is unavailable.")
    key = TaskRun.objects.values_list("idempotency_key", flat=True).get(
        pk=status.root_id
    )
    try:
        identifier = UUID(key)
    except (TypeError, ValueError, AttributeError):
        raise PermissionError("Boundary task identity is unavailable.") from None
    row = CampaignBoundaryOccurrence.objects.filter(
        pk=identifier, campaign_id=status.domain_request_id
    ).first()
    if row is None:
        raise PermissionError("Boundary task binding is unavailable.")
    return row


def _terminal_action(row):
    """A replaced occurrence is cancelled; other terminal results are fulfilled."""
    if row.state == "pending":
        return None
    return "safe_cancel" if row.reason == "boundary_replaced" else "complete"


def _eligible(row):
    """Require current mode/gates and the exact due projection, not a stale hint."""
    scope = boundary_scope(row.campaign_id)
    projection = scope.campaign.active_configuration
    due_at = projection.starts_at if row.kind == "start" else projection.ends_at
    return (
        row.due_at == due_at
        and row.due_at <= scope.instant
        and not CampaignBoundaryOccurrence.objects.filter(
            campaign_id=row.campaign_id,
            kind=row.kind,
            execution_revision__gt=row.execution_revision,
        ).exists()
    )


def admit_boundary(action, status):
    """Recheck current work or terminal proof for every task command and replay."""
    row = _occurrence(status)
    terminal = _terminal_action(row)
    if action in {"lease_expired", "recovery_hint"}:
        return True
    if action in {"complete", "recovery_complete"}:
        return terminal == "complete"
    if action in {"safe_cancel", "recovery_cancel"}:
        return terminal == "safe_cancel"
    if action in {"recovery_retry", "recovery_fail"}:
        plan = recover_boundary(status)
        return plan is not None and plan.action == action
    if action not in {
        "hint",
        "claim",
        "effect",
        "heartbeat",
        "progress",
        "explicit_retry",
        "explicit_retry_replay",
    }:
        return False
    if terminal is not None:
        return action in {"hint", "claim", "heartbeat", "progress"}
    return _eligible(row)


def recover_boundary(status):
    """Use committed outcomes; retry unfinished local work at most five times."""
    row = _occurrence(status)
    if status.state != "abandoned":
        raise PermissionError("Boundary recovery requires abandoned work.")
    terminal = _terminal_action(row)
    if terminal is not None:
        return RecoveryPlan(
            "recovery_cancel" if terminal == "safe_cancel" else "recovery_complete"
        )
    try:
        eligible = _eligible(row)
    except PermissionError:
        return None
    if not eligible:
        return None
    if status.attempt >= 5:
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan(
        "recovery_retry", min(30 * 2 ** max(status.attempt - 1, 0), 600)
    )


def _execute(execution):
    """Apply one ordered campaign transaction, then acknowledge its durable result.

    The lifecycle owner takes installation serialization before work/task locks;
    do not wrap that operation in Execution.effect's already-open transaction.
    Each callback checks the real claim after lock waits. A crash between domain
    commit and TaskRun completion is reconciled from the terminal occurrence.
    """
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError(
            "Boundary execution requires a maintained lifetime."
        )
    with execution.control.lock:
        execution.check()
        with work_transaction():
            row = _occurrence(_status(lock_task_claim(execution.claim)))
            terminal = _terminal_action(row)
        if terminal is None:
            entered = False

            def admit(action, campaign, runtime, subject):
                """Bind the hint once; recheck gates and fences for both effects."""
                nonlocal entered
                execution.check()
                status = _status(lock_task_claim(execution.claim))
                current = _occurrence(status)
                # The compiled handler's first effect admission proves pending
                # eligibility and any bound configuration authority. Later
                # effects retain that installer lock and recheck eligibility.
                if (
                    action not in {Action.START, Action.CLOSE}
                    or campaign.pk != row.campaign_id
                    or (not entered and current.state != "pending")
                    or (
                        not entered
                        and execution.handler.admit("effect", status) is not True
                    )
                    or (entered and not _eligible(current))
                ):
                    raise PermissionError("Boundary execution is no longer admitted.")
                # Close atomically scrubs the population, so it needs more than
                # the generic 60-second transport claim. Renew only after all
                # installer/work/task lock waits and fresh owning admission.
                # This remains the substrate's bounded maximum; every SQL
                # effect still rejects actual expiry and cannot revive a lease.
                execution.heartbeat(seconds=300)
                entered = True

            apply_due_boundaries(
                campaign_id=row.campaign_id,
                task_id=execution.claim.run_id,
                fence=execution.claim.fence,
                actor_id=execution.claim.worker_id,
                correlation_id=execution.correlation_id,
                admit=admit,
            )
    # Recheck the terminal receipt after commit, not the previous Python status.
    with work_transaction():
        row = _occurrence(_status(lock_task_claim(execution.claim)))
        terminal = _terminal_action(row)
    if terminal is None:
        raise StorageInvariantError("Boundary execution has no committed outcome.")
    execution.transition(terminal)


def boundary_handler(*, scheduler=False):
    """Keep scheduler metadata admission separate from executable worker authority."""
    if type(scheduler) is not bool:
        raise TypeError("Boundary handler requires a compiled service role.")

    def unavailable(execution):
        """A scheduler registry entry cannot execute lifecycle work directly."""
        raise PermissionError("The scheduler cannot execute campaign boundaries.")

    return Handler(
        WorkQueue.GENERAL,
        admit_boundary,
        unavailable if scheduler else _execute,
        recover=recover_boundary,
        scope=work_transaction,
    )
