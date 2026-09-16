"""Compiled general-worker export execution with fenced immutable publication."""

import os
from datetime import timedelta
from functools import partial
from pathlib import Path

from django.db import connection

from parishkit.stewardship.campaigns.read_guards import CampaignReadGuard
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.models import TaskRun, TaskRunEvent
from parishkit.stewardship.jobs.ownership import database_now, lock_task_claim
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.storage import StorageInvariantError

from .artifacts import write_artifact
from .charts import render_participation
from .documents import participation_document
from .export_models import (
    ExportAttempt,
    ExportCancellation,
    ExportPublication,
    ExportRequest,
)
from .export_services import TASK_TYPE, admit_campaign, authorize
from .models import CampaignDailyFactSet, CampaignFactPin
from .participation import participation_csv


def bound_request(status):
    """Bind actual persisted task metadata, not an untrusted caller's status view."""
    require_work_order()
    request = ExportRequest.objects.filter(
        pk=status.domain_request_id, task_id=status.root_id
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
        raise PermissionError("Export task ownership is unavailable.")
    return request


def _outcome(request):
    """Task terminality is not publication proof; only immutable domain receipts are."""
    if ExportPublication.objects.filter(request=request).exists():
        return "complete"
    if ExportCancellation.objects.filter(request=request).exists():
        return "cancel"
    return None


def recover_export(status):
    """Retry a fresh artifact attempt or acknowledge an already committed outcome."""
    request = bound_request(status)
    if status.state != "abandoned":
        return None
    outcome = _outcome(request)
    if outcome:
        return RecoveryPlan("recovery_" + outcome)
    if status.attempt >= 5:
        return RecoveryPlan("recovery_fail")
    admit_campaign(request.campaign_id, mutating=True)
    return RecoveryPlan(
        "recovery_retry", min(30 * 2 ** max(status.attempt - 1, 0), 600)
    )


def admit_export(action, status, *, store=None):
    """Schedulers inspect metadata; only the worker can execute with coherent policy."""
    request = bound_request(status)
    outcome = _outcome(request)
    if action in {"lease_expired", "recovery_hint"}:
        return True
    if action.startswith("recovery_"):
        plan = recover_export(status)
        return plan is not None and action == plan.action
    if action == "complete":
        return outcome == "complete"
    if action == "safe_cancel":
        return outcome == "cancel"
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
    if outcome is not None:
        return True
    admit_campaign(request.campaign_id, mutating=True)
    if store is not None:
        authorize(store, request.requester_id, request=request)
    return True


def _abort_render_worker():
    """Hard-stop the isolated solo worker before releasing a timed-out read guard.

    A background worker has no HTTP socket to abort. Merely setting a cooperative
    flag cannot prove a blocked renderer stopped writing files. Terminating this
    single-task process closes its SQL sessions and files; durable attempts and
    TaskRun lease recovery retain the real unfinished outcome. Compose restarts
    the worker. This hook is used only by the finite guard's deadline thread.
    """
    os._exit(70)


def load_document(request):
    """Load exactly one pinned ready generation inside the caller's campaign guard."""
    facts = CampaignDailyFactSet.objects.get(
        pk=request.fact_set_id, campaign_id=request.campaign_id, state="ready"
    )
    if not CampaignFactPin.objects.filter(
        fact_set=facts, parent_kind="export", parent_id=request.pk
    ).exists():
        raise StorageInvariantError("Export calculation pin is unavailable.")
    return participation_document(
        facts,
        parish_name=request.configuration.parish.name,
        browser_timezone=request.browser_timezone,
        requested_at=request.created_at,
    )


def _execute(execution, *, store, root):
    """Record attempt, render under a read guard, then freshly authorize publish."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Exports require maintained worker lifetime.")
    with execution.effect():
        task = lock_task_claim(execution.claim)
        request = bound_request(_status(task))
        outcome = _outcome(request)
        if outcome is not None:
            execution.transition("complete" if outcome == "complete" else "safe_cancel")
            return
        event = TaskRunEvent.objects.get(run=task, fence=task.fence, action="claim")
        attempt = ExportAttempt.objects.create(
            request=request,
            run=task,
            fence=task.fence,
            claim_event=event,
            actor_id=task.worker_id,
            correlation_id=event.pk,
        )

    def authorize_render(guard):
        """No stale requester or source selection grants worker query access."""
        authorize(store, request.requester_id, request=request)
        admit_campaign(request.campaign_id, mutating=True)

    with CampaignReadGuard(
        [request.campaign_id], authorize=authorize_render, abort=_abort_render_worker
    ) as guard:
        document = load_document(request)

        def render(stream):
            """The only compiled renderer consumes the exact pinned document."""
            guard.check()
            execution.check()
            if request.format == "csv":
                participation_csv(document, stream)
            else:
                render_participation(document, stream, format=request.format)
            guard.check()
            execution.check()

        receipt = write_artifact(root, request.campaign_id, attempt.pk, render)
        guard.check()
    with execution.effect():
        request = bound_request(_status(lock_task_claim(execution.claim)))
        if _outcome(request) == "cancel":
            execution.transition("safe_cancel")
            return
        authorize(store, request.requester_id, request=request)
        admit_campaign(request.campaign_id, mutating=True)
        ExportPublication.objects.create(
            request=request,
            attempt=attempt,
            size=receipt.size,
            sha256=receipt.sha256,
            row_count=len(document.days),
            expires_at=database_now() + timedelta(days=7),
            actor_id=execution.claim.worker_id,
            correlation_id=attempt.claim_event_id,
        )
        execution.transition("complete")


def export_handler(*, store=None, root=None, scheduler=False):
    """The registry, not broker payloads, supplies the render and filesystem owner."""
    if type(scheduler) is not bool or (
        not scheduler and (store is None or not isinstance(root, Path))
    ):
        raise TypeError("Export execution requires configured storage and policy.")

    def unavailable(execution):
        """Scheduler metadata authority is never permission to render or write files."""
        raise PermissionError("The scheduler cannot render exports.")

    return Handler(
        WorkQueue.GENERAL,
        partial(admit_export, store=store),
        unavailable if scheduler else partial(_execute, store=store, root=root),
        recover=recover_export,
        scope=work_transaction,
    )
