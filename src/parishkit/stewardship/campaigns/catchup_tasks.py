"""Compiled activation preparation ownership; task terminality is not coverage.

The canonical task may be retried, but its original demand, source and cutoff
never change. Completion is acknowledged only after the preparation owner has
committed its final checkpoint. Scheduler admission remains metadata-only.
"""

from django.db import OperationalError, connection

from parishkit.stewardship.jobs.admission import _scope
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import TaskStatus, _status
from parishkit.stewardship.storage import StorageInvariantError

from .catchup_allocation import TASK_TYPE
from .catchup_errors import CatchUpPreparationHeld
from .credential_models import CampaignCredentialState
from .models import (
    ActivationCatchUpDemand,
    CampaignWorkGate,
    CatchUpCheckpoint,
    CatchUpFailure,
)
from .work_locks import require_work_order, work_transaction


def owned_demand(status):
    """Resolve only exact persisted task metadata and its original bound root."""
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
        raise PermissionError("Catch-up task ownership is unavailable.")
    demand = ActivationCatchUpDemand.objects.filter(
        pk=status.domain_request_id,
        task_root_id=status.root_id,
        source_snapshot_id__isnull=False,
    ).first()
    if demand is None:
        raise PermissionError("Catch-up task binding is unavailable.")
    return demand


def completed(demand):
    """Require the final durable checkpoint, never a task's claimed success."""
    return (
        demand.completed_at is not None
        and CatchUpCheckpoint.objects.filter(
            demand=demand,
            sequence=demand.groups_completed,
            complete=True,
            phase=demand.phase,
            cursor=demand.cursor,
        ).exists()
    )


def eligible(demand):
    """Local preparation may continue through delivery pause and campaign close.

    Neither grants permission to dispatch. Restore, go-live cleanup and purge
    remain independent holds; a configuration replacement is re-evaluated by
    each bounded group rather than pinning obsolete schedules at activation.
    If admission is revoked between effects, do not write failure/progress
    through that hold. Lease abandonment and recovery resume the retained cursor
    after admission returns; only an admitted local group failure records retry.
    """
    scope = _scope(demand.campaign_id)
    return (
        scope.runtime.current_campaign_id == demand.campaign_id
        and scope.runtime.mode == "production"
        and not scope.runtime.restore_review_required
        and scope.campaign is not None
        and scope.campaign.state in {"active", "closed"}
        and not CampaignWorkGate.objects.filter(campaign_id=demand.campaign_id)
        .exclude(state="released")
        .exists()
        and CampaignCredentialState.objects.filter(
            campaign_id=demand.campaign_id, go_live_gate=False
        ).exists()
    )


def recover_catchup(status):
    """Reconcile a committed final checkpoint or retry retained bounded progress."""
    demand = owned_demand(status)
    if status.state != "abandoned":
        return None
    if completed(demand):
        return RecoveryPlan("recovery_complete")
    if not eligible(demand):
        return None
    if status.attempt >= 5:
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan(
        "recovery_retry", min(30 * 2 ** max(status.attempt - 1, 0), 600)
    )


def admit_catchup(action, status):
    """Recheck task identity and independent gates for every command or replay."""
    demand = owned_demand(status)
    if action in {"lease_expired", "recovery_hint"}:
        return True
    if action == "complete":
        return completed(demand)
    if action in {"recovery_complete", "recovery_retry", "recovery_fail"}:
        plan = recover_catchup(status)
        return plan is not None and plan.action == action
    if completed(demand):
        return action in {"hint", "claim", "effect", "heartbeat", "progress"}
    if action in {"retryable_failure", "permanent_failure"}:
        return (
            eligible(demand)
            and CatchUpFailure.objects.filter(
                demand=demand,
                expected_version=demand.version - 1,
                task_id=status.run_id,
                fence=status.fence,
                actor_id=status.worker_id,
                code=demand.failure_code,
            ).exists()
            and (action == "permanent_failure") == (status.attempt >= 5)
        )
    return eligible(demand) and action in {
        "hint",
        "claim",
        "effect",
        "heartbeat",
        "progress",
        "explicit_retry",
        "explicit_retry_replay",
    }


def _execute(execution):
    """Commit bounded preparation effects, then acknowledge their final receipt."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Catch-up requires a maintained worker lifetime.")
    # The exact finite target inventory may change with configuration while
    # preparation runs. Do not publish a made-up denominator or premature 100%.
    # The durable demand exposes actual checkpoint/item counts independently.
    execution.progress(0, 0, phase=TaskPhase.PREPARING)
    try:
        _prepare(execution)
    except (OperationalError, CatchUpPreparationHeld) as error:
        # Only known safe local failures use automatic retry. Programming,
        # permission, constraint and ownership failures retain their original
        # exception and cannot manufacture completion or a current worker.
        with execution.effect():
            status = _status(lock_task_claim(execution.claim))
            demand = owned_demand(status)
            if completed(demand):
                # An acknowledgment failed after commit; normal recovery reads
                # the final receipt. Never append failure to completed demand.
                raise
            CatchUpFailure.objects.create(
                demand=demand,
                expected_version=demand.version,
                task_id=status.run_id,
                fence=status.fence,
                code="recovery_required"
                if isinstance(error, CatchUpPreparationHeld)
                else "outcome_failed",
                actor_id=status.worker_id,
                correlation_id=status.run_id,
            )
            exhausted = status.attempt >= 5
            execution.transition(
                "permanent_failure" if exhausted else "retryable_failure",
                **(
                    {}
                    if exhausted
                    else {
                        "retry_seconds": min(30 * 2 ** max(status.attempt - 1, 0), 600)
                    }
                ),
            )


def _prepare(execution):
    """Keep each page's effects and receipt in one independently committed scope."""
    from .catchup_preparation import prepare_batch

    while True:
        with execution.effect():
            demand = owned_demand(_status(lock_task_claim(execution.claim)))
            if completed(demand):
                execution.transition("complete")
                return
            execution.heartbeat(seconds=60)
            prepare_batch(demand, execution.claim)


def catchup_handler(*, scheduler=False):
    """Expose recovery metadata to scheduler without a preparation execution port."""
    if type(scheduler) is not bool:
        raise TypeError("Catch-up requires a compiled service role.")

    def unavailable(execution):
        """A scheduler registry value cannot execute preparation directly."""
        raise PermissionError("The scheduler cannot execute activation catch-up.")

    return Handler(
        WorkQueue.GENERAL,
        admit_catchup,
        unavailable if scheduler else _execute,
        recover=recover_catchup,
        scope=work_transaction,
    )
