"""Compiled exact-input generation work and atomic handoff to the export owner."""

from functools import partial
from uuid import uuid4

from django.db import connection

from parishkit.stewardship.accounts.policy import Capability
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status, enqueue
from parishkit.stewardship.storage import StorageInvariantError

from .exact_models import (
    ExactExportCancellation,
    ExactExportRequest,
    ExactExportResolution,
)
from .exact_services import TASK_TYPE
from .export_models import ExportRequest
from .export_services import admit_campaign, authorize
from .facts import begin_fact_set, fact_inputs
from .materialization import materialize_fact_set
from .models import CampaignDailyFactSet
from .recovery import recover_fact_set
from .retention import pin_facts


def bound_request(status):
    """Only persisted metadata binds broker hints to the retained requester."""
    require_work_order()
    request = ExactExportRequest.objects.filter(
        pk=status.domain_request_id,
        task_id=status.root_id,
    ).first()
    if (
        request is None
        or status.task_type != TASK_TYPE
        or not TaskRun.objects.filter(
            pk=status.run_id,
            root_id=request.task_id,
            task_type=TASK_TYPE,
            domain_request_id=request.pk,
            state=status.state,
            version=status.version,
            fence=status.fence,
            worker_id=status.worker_id,
        ).exists()
    ):
        raise PermissionError("Exact export ownership is unavailable.")
    return request


def _outcome(request):
    """Receipt truth, rather than TaskRun terminality, controls recovery."""
    if ExactExportResolution.objects.filter(request=request).exists():
        return "complete"
    if ExactExportCancellation.objects.filter(request=request).exists():
        return "cancel"
    return None


def recover_exact(status):
    """Preserve frozen input/root retry budgets across process interruption."""
    request = bound_request(status)
    if status.state != "abandoned":
        return None
    outcome = _outcome(request)
    if outcome:
        return RecoveryPlan("recovery_" + outcome)
    if status.attempt >= 5:
        return RecoveryPlan("recovery_fail")
    try:
        admit_campaign(request.campaign_id, mutating=True)
    except PermissionError:
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_export_authorized_v1(%s)", (request.requester_id,)
        )
        if not cursor.fetchone()[0]:
            # No provider effects are uncertain here. Terminalize the revoked
            # owner so another admitted consumer can resume its immutable key.
            return RecoveryPlan("recovery_fail")
    return RecoveryPlan(
        "recovery_retry", min(30 * 2 ** max(status.attempt - 1, 0), 600)
    )


def admit_exact(action, status, *, store=None):
    """Serialize allocation without preempting already running builders."""
    request = bound_request(status)
    outcome = _outcome(request)
    if action == "lease_expired":
        return True
    if action == "recovery_hint":
        # Fence an expired lease even during a hold; already-abandoned work
        # only needs another hint when there is an actionable recovery plan.
        return status.state == "running" or recover_exact(status) is not None
    if action.startswith("recovery_"):
        plan = recover_exact(status)
        return plan is not None and plan.action == action
    if action in {"complete", "safe_cancel"}:
        return outcome == {"complete": "complete", "safe_cancel": "cancel"}[action]
    if action not in {
        "hint",
        "claim",
        "effect",
        "heartbeat",
        "progress",
        "explicit_retry",
        "explicit_retry_replay",
        "permanent_failure",
        "retryable_failure",
    }:
        return False
    if outcome is not None:
        return True
    admit_campaign(request.campaign_id, mutating=True)
    if store is not None:
        authorize(store, request.requester_id, request=request)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_export_authorized_v1(%s)", (request.requester_id,)
        )
        if not cursor.fetchone()[0]:
            return False
        if action in {"hint", "claim"}:
            cursor.execute("SELECT stewardship_exact_claimable_v1(%s)", (request.pk,))
            return cursor.fetchone()[0]
    return True


def _handoff(request, facts, execution):
    """Commit ready-input pin, renderer root and completion proof as one effect."""
    identifier = uuid4()
    # The deferred export FK permits the proof to precede its child. Its commit
    # guard requires the matching pinned child AND successful parent task.
    ExactExportResolution.objects.create(
        request=request,
        export_id=identifier,
        fact_set=facts,
        run_id=execution.claim.run_id,
        fence=execution.claim.fence,
        worker_id=execution.claim.worker_id,
        actor_id=execution.claim.worker_id,
        correlation_id=execution.correlation_id,
    )
    task = enqueue(
        task_type="report_export",
        domain_request_id=identifier,
        actor_id=request.requester_id,
        correlation_id=execution.correlation_id,
        idempotency_key=identifier,
        admit=lambda *args: True,
    )
    export = ExportRequest.objects.create(
        id=identifier,
        campaign_id=request.campaign_id,
        requester_id=request.requester_id,
        request_key=identifier,
        task_id=task.run_id,
        fact_set=facts,
        configuration_id=request.configuration_id,
        report="participation",
        format=request.format,
        browser_timezone=request.browser_timezone,
        parameters={
            "population_scope": request.population_scope,
            "sort": "date_asc",
            "filters": {},
            "selected_ids": [],
        },
        authorization_scope={"capability": Capability.CAMPAIGN_REPORT.value},
        actor_id=request.requester_id,
        correlation_id=execution.correlation_id,
    )
    pin_facts(
        facts.pk, parent_kind="export", parent_id=export.pk, admit=lambda *args: True
    )
    execution.transition("complete")


def _execute(execution, *, store):
    """Build the frozen key; terminal exact/digest owners permit takeover.

    Neither owner retains an ordinary materialization's frozen debounce demand,
    which must instead be released by that materialization's own recovery.
    """
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Exact reports require maintained worker lifetime.")

    def admit(action, inputs):
        """Every chunk uses fresh task and requester authority for the original key."""
        execution.check()
        request = bound_request(_status(lock_task_claim(execution.claim)))
        authorize(store, request.requester_id, request=request)
        admit_campaign(request.campaign_id, mutating=True)
        return _outcome(request) is None and inputs == fact_inputs(request)

    with execution.effect():
        request = bound_request(_status(lock_task_claim(execution.claim)))
        outcome = _outcome(request)
        if outcome is not None:
            execution.transition("complete" if outcome == "complete" else "safe_cancel")
            return
        inputs = fact_inputs(request)
        facts = begin_fact_set(inputs, execution.claim, admit=admit)
        if facts.state != "ready":
            owner = TaskRun.objects.get(pk=facts.task_id)
            if (
                owner.root_id != request.task_id
                and TaskRun.objects.filter(
                    root_id=owner.root_id, state__in=NONTERMINAL_STATES
                ).exists()
            ):
                # An explicit owner retry may arrive after our claim commits.
                # Recheck under the work lock; wait without replacing its fence.
                execution.transition("retryable_failure", retry_seconds=5)
                return
            if owner.task_type not in {TASK_TYPE, "daily_digest_prepare"}:
                # Ordinary recovery must also release its frozen demand. Exact
                # work cannot steal that checkpoint or silently clear the window.
                execution.transition("permanent_failure")
                return
            if (facts.task_id, facts.task_fence, facts.worker_id) != (
                execution.claim.run_id,
                execution.claim.fence,
                execution.claim.worker_id,
            ):
                facts = recover_fact_set(facts.pk, execution.claim, admit=admit)
    if facts.state != "ready":
        try:
            materialize_fact_set(facts.pk, execution.claim, admit=admit)
        except PermissionError:
            # Cancellation at a chunk boundary is a normal stop, not a crashed
            # worker. Other admission failures must retain their real meaning.
            with execution.effect():
                request = bound_request(_status(lock_task_claim(execution.claim)))
                if _outcome(request) != "cancel":
                    raise
                execution.transition("safe_cancel")
            return
    with execution.effect():
        request = bound_request(_status(lock_task_claim(execution.claim)))
        if _outcome(request) == "cancel":
            execution.transition("safe_cancel")
            return
        facts = CampaignDailyFactSet.objects.select_for_update().get(
            pk=facts.pk, state="ready"
        )
        _handoff(request, facts, execution)


def exact_handler(*, store=None, scheduler=False):
    """Only the configured general worker can calculate and hand off exports."""
    if type(scheduler) is not bool or (not scheduler and store is None):
        raise TypeError("Exact report execution requires configured policy.")

    def unavailable(execution):
        """Metadata access is not permission to build or render report data."""
        raise PermissionError("The scheduler cannot build exact reports.")

    return Handler(
        WorkQueue.GENERAL,
        partial(admit_exact, store=store),
        unavailable if scheduler else partial(_execute, store=store),
        recover=recover_exact,
        scope=work_transaction,
    )
