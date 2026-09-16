"""Bounded artifact expiry/crash cleanup, preserving requests, receipts and pins."""

from pathlib import Path
from uuid import uuid4

from django.db import OperationalError, connection
from django.db.models import BooleanField, Exists, Func, OuterRef, Value

from parishkit.config import ConfigError
from parishkit.stewardship.audit.schemas import ContextKind, Outcome
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.campaigns.read_guards import acquire_campaign_drain
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.jobs.storage import enqueue, retry_failed
from parishkit.stewardship.observability import Event
from parishkit.stewardship.storage import StorageInvariantError

from .artifacts import remove_attempt_artifacts
from .export_models import ExportArtifactCleanup, ExportAttempt
from .export_services import admit_campaign, authorize

TASK_TYPE = "report_export_cleanup"
MAX_CLEANUP_ATTEMPTS = 5


def _attempt(status, *, creating=False):
    """A cleanup task owns one retained attempt, never an arbitrary file UUID."""
    require_work_order()
    attempt = (
        ExportAttempt.objects.select_related("request")
        .filter(pk=status.domain_request_id)
        .first()
    )
    if (
        attempt is None
        or status.task_type != TASK_TYPE
        or (
            not creating
            and not TaskRun.objects.filter(
                pk=status.run_id,
                root_id=status.root_id,
                task_type=TASK_TYPE,
                domain_request_id=attempt.pk,
                version=status.version,
                state=status.state,
                fence=status.fence,
                worker_id=status.worker_id,
            ).exists()
        )
    ):
        raise PermissionError("Export cleanup ownership is unavailable.")
    return attempt


def _complete(attempt):
    """A durable removal receipt is the only proof of cleanup completion."""
    return ExportArtifactCleanup.objects.filter(attempt=attempt).exists()


def _eligible(attempt):
    """A current renderer or unexpired publication always prevents file removal."""
    admit_campaign(attempt.request.campaign_id, mutating=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_export_disposable_v1(%s)", (attempt.pk,))
        return cursor.fetchone() == (True,)


def recover_cleanup(status):
    """Retry idempotent removal after a crash, never infer success from task state."""
    attempt = _attempt(status)
    if status.state != "abandoned":
        return None
    if _complete(attempt):
        return RecoveryPlan("recovery_complete")
    if not _eligible(attempt):
        return None
    return (
        RecoveryPlan("recovery_fail")
        if status.attempt >= MAX_CLEANUP_ATTEMPTS
        else RecoveryPlan("recovery_retry", 60)
    )


def admit_cleanup(action, status):
    """Only the file-owning worker can create a receipt; scheduler owns hints only."""
    attempt = _attempt(status, creating=action == "enqueue")
    if action == "permanent_failure":
        return status.attempt >= MAX_CLEANUP_ATTEMPTS
    if action == "retryable_failure":
        return status.attempt < MAX_CLEANUP_ATTEMPTS
    if action in {
        "lease_expired",
        "recovery_hint",
    }:
        return True
    if action in {"complete", "recovery_complete"}:
        return _complete(attempt)
    if action in {"recovery_retry", "recovery_fail"}:
        plan = recover_cleanup(status)
        return plan is not None and action == plan.action
    return action in {
        "enqueue",
        "hint",
        "claim",
        "effect",
        "heartbeat",
        "progress",
        "explicit_retry",
        "explicit_retry_replay",
    } and (_complete(attempt) or _eligible(attempt))


def _after_transition(action, status):
    """Persist a privacy-safe critical signal with the owning terminal transition.

    Do not manufacture new automatic roots indefinitely after bounded retries.
    The Admin operational log/task history identifies the failed cleanup; BG-10
    owns notification transport and Phase 5 owns additional report-job controls.
    Only the owning dispatcher invokes this after a real journal transition,
    inside its transaction. Admission probes are pure; a failed transaction
    rolls the alert back together with its state/event changes.
    """
    if action not in {"permanent_failure", "recovery_fail"}:
        return
    _attempt(status)
    if status.state != "failed" or status.attempt < MAX_CLEANUP_ATTEMPTS:
        raise StorageInvariantError("Cleanup exhaustion requires a failed run.")
    operational(
        Event.TASK_FAILED,
        level="CRITICAL",
        schema=ContextKind.TASK,
        context={"task_id": status.run_id, "outcome": Outcome.FAILED},
    )


def retry_cleanup(store, user_id, attempt_id, *, request_key, run_id=None):
    """An Admin may explicitly retry the same bounded cleanup root after repair."""
    with work_transaction():
        principal = authorize(store, user_id)
        if "administrator" not in principal.roles:
            raise PermissionError("Export cleanup requires an Administrator.")
        attempt = ExportAttempt.objects.select_related("request").get(pk=attempt_id)
        admit_campaign(attempt.request.campaign_id, mutating=True)
        # Production uses the one canonical enqueue key per attempt. Scope all
        # retry selection and replay to that root, like the TaskRun journal does.
        root = TaskRun.objects.get(
            task_type=TASK_TYPE,
            domain_request_id=attempt.pk,
            idempotency_key=str(attempt.pk),
            retry_sequence=0,
        )
        runs = root.chain_runs
        if run_id is None:
            previous = runs.filter(retry_command_id=request_key).first()
            latest = runs.order_by("-retry_sequence").first()
            run_id = previous.parent_id if previous is not None else latest.pk
        elif not runs.filter(pk=run_id).exists():
            raise PermissionError("This run does not belong to the cleanup root.")
        return retry_failed(
            run_id=run_id,
            command_id=request_key,
            actor_id=user_id,
            correlation_id=uuid4(),
            admit=admit_cleanup,
        )


def cleanup_handler(root=None):
    """Filesystem ownership is compiled by runtime; schedulers receive no root."""
    if root is not None and (not isinstance(root, Path) or not root.is_absolute()):
        raise ValueError("Export cleanup requires an admitted absolute reports root.")

    def execute(execution):
        """Drain readers before exact-file removal and atomically record its receipt."""
        if root is None:
            raise PermissionError("The scheduler cannot remove export files.")
        # Filesystem removal is only an unlink of at most two known files, not
        # content I/O. Keep the exclusive read barrier through receipt commit.
        try:
            with execution.effect():
                task = TaskRun.objects.get(pk=execution.claim.run_id)
                attempt = ExportAttempt.objects.select_related("request").get(
                    pk=task.domain_request_id
                )
                if not _complete(attempt):
                    acquire_campaign_drain(
                        [attempt.request.campaign_id], wait_seconds=5
                    )
                    execution.check()
                    lock_task_claim(execution.claim)
                    if not _eligible(attempt):
                        raise PermissionError("Export cleanup is no longer admitted.")
                    remove_attempt_artifacts(
                        root, attempt.request.campaign_id, attempt.pk
                    )
                    ExportArtifactCleanup.objects.create(
                        attempt=attempt,
                        task=task,
                        fence=execution.claim.fence,
                        actor_id=execution.claim.worker_id,
                    )
                execution.transition("complete")
        except (ConfigError, OperationalError):
            task = TaskRun.objects.get(pk=execution.claim.run_id)
            exhausted = task.attempt >= MAX_CLEANUP_ATTEMPTS
            execution.transition(
                "permanent_failure" if exhausted else "retryable_failure",
                **({} if exhausted else {"retry_seconds": 60}),
            )

    return Handler(
        WorkQueue.GENERAL,
        admit_cleanup,
        execute,
        recover=recover_cleanup,
        scope=work_transaction,
        after_transition=_after_transition,
    )


def produce_cleanup(guard, *, limit=20):
    """Bound candidates before enqueue; one root per attempt prevents retry storms."""
    if (
        not isinstance(guard, SchedulerGuard)
        or type(limit) is not int
        or not 1 <= limit <= 100
    ):
        raise TypeError("Export cleanup requires an owned bounded scheduler input.")
    if connection.in_atomic_block:
        raise StorageInvariantError("Export cleanup production owns its transaction.")
    guard.check()
    with work_transaction():
        existing = TaskRun.objects.filter(
            task_type=TASK_TYPE, domain_request_id=OuterRef("pk")
        )
        candidates = (
            ExportAttempt.objects.alias(
                disposable=Func(
                    "pk",
                    function="stewardship_export_disposable_v1",
                    output_field=BooleanField(),
                ),
                admitted=Func(
                    "request__campaign_id",
                    Value(True),
                    function="stewardship_export_admitted_v1",
                    output_field=BooleanField(),
                ),
            )
            .filter(disposable=True, admitted=True)
            .filter(~Exists(existing))
            .select_related("request")
            .order_by("created_at", "pk")[:limit]
        )
        result = []
        for attempt in candidates:
            guard.check()
            try:
                admit_campaign(attempt.request.campaign_id, mutating=True)
            except PermissionError:
                continue
            result.append(
                enqueue(
                    task_type=TASK_TYPE,
                    domain_request_id=attempt.pk,
                    actor_id=None,
                    correlation_id=attempt.pk,
                    idempotency_key=attempt.pk,
                    admit=admit_cleanup,
                )
            )
        return tuple(result)
