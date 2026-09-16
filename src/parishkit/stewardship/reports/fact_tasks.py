"""Compiled ordinary fact rebuilds, retaining frozen inputs through crash recovery.

One task root belongs to one demand window. Later hints update the durable demand,
not the running generation. Domain completion and task completion commit together;
an interrupted publication resumes its original generation before newer demand.
"""

from django.db import connection

from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now, lock_task_claim
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.storage import StorageInvariantError

from .demand import claim_rebuild, complete_rebuild, requested_inputs
from .export_services import admit_campaign
from .facts import FactUnavailable, fact_inputs
from .materialization import materialize_fact_set
from .models import CampaignDailyFactSet, CampaignFactRebuildDemand, FactBuildReceipt
from .recovery import recover_rebuild

TASK_TYPE = "report_facts"


def bound_demand(status):
    """Accept persisted task identity only; arbitrary broker metadata has no scope."""
    require_work_order()
    if (
        status.task_type != TASK_TYPE
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
        raise PermissionError("Fact task ownership is unavailable.")
    demand = CampaignFactRebuildDemand.objects.filter(
        pk=status.domain_request_id
    ).first()
    if demand is None:
        raise PermissionError("Fact task demand is unavailable.")
    return demand


def _generation(status):
    """Recovery uses the root's frozen generation, never the latest requested tuple."""
    demand = bound_demand(status)
    if (
        demand.claimed_generation_id is not None
        and TaskRun.objects.filter(
            pk=demand.claimed_task_id, root_id=status.root_id
        ).exists()
    ):
        return demand.claimed_generation
    return CampaignDailyFactSet.objects.filter(task__root_id=status.root_id).first()


def _completed(status, demand):
    """A receipt survives later fact compaction and exact-generation reuse."""
    return FactBuildReceipt.objects.filter(
        task_id=status.root_id, demand=demand
    ).exists()


def recover_facts(status):
    """Retry immutable local calculations, or acknowledge their committed outcome."""
    demand = bound_demand(status)
    if status.state != "abandoned":
        return None
    if _completed(status, demand):
        return RecoveryPlan("recovery_complete")
    try:
        admit_campaign(demand.campaign_id, mutating=True)
    except PermissionError:
        return None
    if status.attempt >= 5:
        # Keep the frozen demand/checkpoint for an explicit linked retry. A new
        # root here would discard ownership and silently reset the retry budget.
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan(
        "recovery_retry", min(30 * 2 ** max(status.attempt - 1, 0), 600)
    )


def admit_facts(action, status):
    """Gate every task effect against current restore/purge/go-live authority."""
    demand = bound_demand(status)
    if action in {"lease_expired", "recovery_hint"}:
        return True
    if action.startswith("recovery_"):
        plan = recover_facts(status)
        return plan is not None and plan.action == action
    if action == "complete":
        return _completed(status, demand)
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
    if _completed(status, demand):
        return True
    admit_campaign(demand.campaign_id, mutating=True)
    if demand.claimed_task_id is not None:
        return TaskRun.objects.filter(
            pk=demand.claimed_task_id, root_id=status.root_id
        ).exists()
    return demand.pending_due_at is not None and demand.pending_due_at <= database_now()


def _execute(execution):
    """Freeze once, calculate outside write locks, then release only this revision."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError(
            "Fact rebuilding requires maintained worker lifetime."
        )

    def admit(action, inputs):
        """A storage callback rechecks the actual task and its exact domain inputs."""
        execution.check()
        status = _status(lock_task_claim(execution.claim))
        demand = bound_demand(status)
        admit_campaign(demand.campaign_id, mutating=True)
        if (
            inputs.campaign_id != demand.campaign_id
            or inputs.population_scope != demand.population_scope
        ):
            return False
        if action in {"claim", "create"}:
            return (
                requested_inputs(demand) == inputs
                and demand.claimed_generation_id is None
            )
        generation = _generation(status)
        return generation is not None and fact_inputs(generation) == inputs

    with execution.effect():
        status = _status(lock_task_claim(execution.claim))
        demand = bound_demand(status)
        if _completed(status, demand):
            execution.transition("complete")
            return
        if demand.claimed_generation_id is None:
            demand = claim_rebuild(
                demand.campaign_id,
                demand.population_scope,
                execution.claim,
                admit=admit,
            )
            if demand is None:
                raise FactUnavailable("Fact rebuild window is no longer claimable.")
        else:
            demand = recover_rebuild(
                demand.pk,
                execution.claim,
                revision=demand.claimed_revision,
                admit=admit,
            )
        generation = demand.claimed_generation
        revision = demand.claimed_revision
    if generation.state != "ready":
        materialize_fact_set(generation.pk, execution.claim, admit=admit)
    with execution.effect():
        task = lock_task_claim(execution.claim)
        FactBuildReceipt.objects.create(
            task_id=task.root_id,
            run=task,
            demand=demand,
            revision=revision,
            task_fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            fact_set_id=generation.pk,
            actor_id=execution.claim.worker_id,
            correlation_id=execution.correlation_id,
        )
        complete_rebuild(
            demand.pk, execution.claim, revision=revision, admit=admit, interactive=True
        )
        execution.transition("complete")


def fact_handler(*, scheduler=False):
    """Only the general worker executes calculations; the scheduler reads metadata."""
    if type(scheduler) is not bool:
        raise TypeError("Fact execution requires a compiled service role.")

    def unavailable(execution):
        """A metadata registry is not permission to calculate or publish reports."""
        raise PermissionError("The scheduler cannot build report facts.")

    return Handler(
        WorkQueue.GENERAL,
        admit_facts,
        unavailable if scheduler else _execute,
        recover=recover_facts,
        scope=work_transaction,
    )
