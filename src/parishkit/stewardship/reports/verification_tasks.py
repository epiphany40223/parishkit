"""Fenced recalculation checks with bounded read lifetimes and durable outcomes."""

from django.db import connection

from parishkit.stewardship.audit.schemas import Action, ActorKind, ContextKind, Outcome
from parishkit.stewardship.audit.services import operational, record_action
from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.campaigns.read_guards import CampaignReadGuard
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.observability import Event
from parishkit.stewardship.storage import StorageInvariantError

from .export_services import admit_campaign
from .export_tasks import _abort_render_worker
from .facts import FACT_READ_NAMESPACE, FactUnavailable, fact_inputs
from .materialization import verify_fact_set
from .models import CampaignDailyFactSet
from .verification_models import FactVerificationRequest, FactVerificationResult
from .verification_production import INPUT_FIELDS, TASK_TYPE


def bound_request(status):
    """Bind immutable request and actual persisted task identity, not broker claims."""
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
        raise PermissionError("Fact verification ownership is unavailable.")
    request = FactVerificationRequest.objects.filter(
        pk=status.domain_request_id, task_id=status.root_id
    ).first()
    if request is None:
        raise PermissionError("Fact verification request is unavailable.")
    return request


def _complete(request):
    """Both matched and drift are completed checks; neither is inferred from state."""
    return FactVerificationResult.objects.filter(request=request).exists()


def _available(request, *, protect=False):
    """Explicit retry must atomically recover its exact generation or fail closed."""
    if protect:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_xact_lock_shared(%s,hashtext(%s))",
                (FACT_READ_NAMESPACE, str(request.fact_set_id)),
            )
            if not cursor.fetchone()[0]:
                return False
    return CampaignDailyFactSet.objects.filter(
        pk=request.fact_set_id,
        state="ready",
        **{field: getattr(request, field) for field in INPUT_FIELDS},
    ).exists()


def _recovery_plan(status, request):
    """Held abandoned work stays quiet; lost inputs never count as a match."""
    if status.state != "abandoned":
        return None
    if _complete(request):
        return RecoveryPlan("recovery_complete")
    try:
        admit_campaign(request.campaign_id, mutating=True)
    except PermissionError:
        return None
    if status.attempt >= 5 or not _available(request):
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan(
        "recovery_retry", min(30 * 2 ** max(status.attempt - 1, 0), 600)
    )


def recover_verification(status):
    """Only the original request supplies recovery inputs and completion proof."""
    return _recovery_plan(status, bound_request(status))


def admit_verification(action, status):
    """Recheck campaign gates and ownership before every task transition/effect."""
    request = bound_request(status)
    if action == "lease_expired":
        return True
    if action == "recovery_hint":
        return status.state == "running" or _recovery_plan(status, request) is not None
    if action.startswith("recovery_"):
        plan = _recovery_plan(status, request)
        return plan is not None and plan.action == action
    if action == "complete":
        return _complete(request)
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
    admit_campaign(request.campaign_id, mutating=True)
    if action == "explicit_retry_replay":
        return True
    return _complete(request) or _available(request, protect=action == "explicit_retry")


def _after_transition(action, status):
    """Record failed verification visibly, without copying facts or raw exceptions."""
    if action == "recovery_fail":
        bound_request(status)
        operational(
            Event.TASK_FAILED,
            level="CRITICAL",
            schema=ContextKind.TASK,
            context={"task_id": status.run_id, "outcome": Outcome.FAILED},
        )


def _execute(execution):
    """Recalculate outside write locks; commit only freshly fenced completed checks."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Verification requires maintained worker lifetime.")
    with execution.effect():
        request = bound_request(_status(lock_task_claim(execution.claim)))
        if _complete(request):
            execution.transition("complete")
            return

    def authorize(guard):
        """SELECT-only admission is compatible with the guarded read transaction."""
        execution.check()
        admit_campaign(request.campaign_id, mutating=True)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_fact_live(%s,%s,%s)",
                (
                    execution.claim.run_id,
                    execution.claim.fence,
                    execution.claim.worker_id,
                ),
            )
            if not cursor.fetchone()[0]:
                raise PermissionError("Verification no longer owns a live task.")
        if not _available(request):
            raise FactUnavailable(
                "The original verification generation is unavailable."
            )

    with CampaignReadGuard(
        [request.campaign_id], authorize=authorize, abort=_abort_render_worker
    ) as guard:

        def admit_read(action, inputs):
            """The held generation and frozen request must describe the same inputs."""
            guard.check()
            execution.check()
            return action == "read" and inputs == fact_inputs(request)

        differences = verify_fact_set(request.fact_set_id, admit=admit_read)
        guard.check()
        execution.check()

    with execution.effect():
        task = lock_task_claim(execution.claim)
        request = bound_request(_status(task))
        result = FactVerificationResult.objects.create(
            request=request,
            run=task,
            fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            actor_id=execution.claim.worker_id,
            correlation_id=execution.correlation_id,
            outcome="drift" if differences else "matched",
            differing_days=len(differences),
        )
        record_action(
            Action.FACTS_VERIFIED,
            actor_kind=ActorKind.SYSTEM,
            actor_id=execution.claim.worker_id,
            subject_id=result.pk,
            campaign_id=request.campaign_id,
            parish_id=CampaignConfiguration.objects.values_list(
                "configuration__parish__id", flat=True
            ).get(pk=request.timezone_configuration_id),
            context={
                "count": len(differences),
                "outcome": Outcome.SUCCEEDED,
            },
        )
        if differences:
            operational(
                Event.FACT_DRIFT,
                level="CRITICAL",
                schema=ContextKind.TASK,
                context={
                    "task_id": task.pk,
                    "count": len(differences),
                    "outcome": Outcome.FAILED,
                },
            )
        execution.transition("complete")


def verification_handler(*, scheduler=False):
    """Schedulers manage metadata only; the general worker executes the check."""
    if type(scheduler) is not bool:
        raise TypeError("Verification requires a compiled service role.")

    def unavailable(execution):
        """The metadata registry cannot calculate or read response/financial values."""
        raise PermissionError("The scheduler cannot verify report facts.")

    return Handler(
        WorkQueue.GENERAL,
        admit_verification,
        unavailable if scheduler else _execute,
        recover=recover_verification,
        scope=work_transaction,
        after_transition=_after_transition,
    )
