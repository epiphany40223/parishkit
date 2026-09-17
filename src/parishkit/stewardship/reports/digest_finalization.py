"""Metadata-only completion after a later provider reconciliation accepts mail.

Normal delivery settles the aggregate in its final accepted child's transaction.
An Admin may instead resolve an abandoned attempt after its worker is gone. That
observation must commit without fabricating a live provider owner. This separate
compiled task obtains a fresh metadata claim; SQL independently proves the full
resolved cohort and records the ordinary running/succeeded occurrence history.
"""

from uuid import UUID, uuid4

from django.db import connection

from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import lock_task_claim
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import SchedulerGuard
from parishkit.stewardship.jobs.storage import _status, enqueue
from parishkit.stewardship.storage import StorageInvariantError

from .digest_models import DailyDigestPreparation

TASK_TYPE = "daily_digest_finalize"


def _preparation(status):
    """Bind persisted task metadata independently of mutable completion state."""
    require_work_order()
    if (
        status.task_type != TASK_TYPE
        or not TaskRun.objects.filter(
            pk=status.run_id,
            root_id=status.root_id,
            domain_request_id=status.domain_request_id,
            task_type=TASK_TYPE,
            state=status.state,
            version=status.version,
            fence=status.fence,
            worker_id=status.worker_id,
        ).exists()
        or not TaskRun.objects.filter(
            pk=status.root_id,
            task_type=TASK_TYPE,
            domain_request_id=status.domain_request_id,
            idempotency_key=str(status.domain_request_id),
        ).exists()
    ):
        raise PermissionError("Daily finalization Task binding differs.")
    return DailyDigestPreparation.objects.get(pk=status.domain_request_id)


def _outcome(status):
    """Validate exact persisted metadata; no recipients or report values are read."""
    preparation = _preparation(status)
    occurrence = ScheduleOccurrence.objects.filter(pk=preparation.occurrence_id).first()
    if occurrence is None or occurrence.state in {"skipped", "coalesced"}:
        return "safe_cancel"
    if occurrence.state == "succeeded":
        return "complete"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_daily_digest_resolved_v1(%s)", [preparation.pk]
        )
        if occurrence.state != "pending" or not cursor.fetchone()[0]:
            raise PermissionError(
                "Daily finalization requires the entire resolved cohort."
            )
    return None


def recover_finalization(status):
    """A crash after the claim cannot duplicate already committed completion."""
    if status.state != "abandoned":
        return None
    _preparation(status)
    try:
        outcome = _outcome(status)
    except PermissionError:
        # A broken completion proof must not strand an expired owner forever.
        # Fail visibly; only an explicit, freshly admitted retry may resume it.
        return RecoveryPlan("recovery_fail")
    if outcome is not None:
        return RecoveryPlan(
            "recovery_complete" if outcome == "complete" else "recovery_cancel"
        )
    if status.attempt >= 5:
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan("recovery_retry", retry_seconds=30)


def admit_finalization(action, status):
    """Hints and recovery never grant report/body or provider access."""
    _preparation(status)
    if action == "lease_expired":
        return True
    if action == "recovery_hint":
        return status.state == "running" or recover_finalization(status) is not None
    if action.startswith("recovery_"):
        plan = recover_finalization(status)
        return plan is not None and action == plan.action
    if action in {"permanent_failure", "retryable_failure"}:
        return True
    outcome = _outcome(status)
    if action in {"complete", "safe_cancel"}:
        return action == outcome
    return action in {
        "hint",
        "claim",
        "effect",
        "heartbeat",
        "progress",
        "explicit_retry",
        "explicit_retry_replay",
    }


def _execute(execution):
    """The claim's private SQL trigger has already committed cohort completion."""
    with execution.effect():
        outcome = _outcome(_status(lock_task_claim(execution.claim)))
    if outcome is None:
        raise StorageInvariantError(
            "Daily finalization did not settle its resolved cohort."
        )
    execution.transition(outcome)


def finalization_handler(*, scheduler=False):
    """A provider-free handler safely runs with general-worker metadata authority."""
    if type(scheduler) is not bool:
        raise TypeError("Daily finalization requires an admitted runtime role.")

    def unavailable(execution):
        """Scheduler admission cannot execute even provider-free worker effects."""
        raise PermissionError("The scheduler cannot execute daily finalization.")

    return Handler(
        WorkQueue.GENERAL,
        admit_finalization,
        unavailable if scheduler else _execute,
        recover=recover_finalization,
        scope=work_transaction,
    )


class DailyDigestFinalizeProducer:
    """Allocate missing finalizers from the bounded, value-free resolved-cohort view."""

    def __init__(self, worker_id):
        """Bind a service identity, never a browser-supplied claimed permission."""
        if not isinstance(worker_id, UUID):
            raise TypeError("Daily finalization requires a scheduler identity.")
        self.worker_id = worker_id

    def __call__(self, guard):
        """One root per preparation; retry stays with the existing durable root."""
        if not isinstance(guard, SchedulerGuard) or connection.in_atomic_block:
            raise StorageInvariantError(
                "Daily finalization requires scheduler ownership."
            )
        guard.check()
        with work_transaction(), connection.cursor() as cursor:
            cursor.execute(
                """SELECT p.id FROM stewardship_daily_digest_completion_ready ready
                JOIN stewardship_daily_digest_preparation p ON p.id=ready.preparation_id
                JOIN stewardship_schedule_occurrence o ON o.id=p.occurrence_id
                WHERE o.state='pending' AND NOT EXISTS(
                    SELECT 1 FROM stewardship_task_run t
                    WHERE t.task_type=%s AND t.domain_request_id=p.id)
                ORDER BY p.id LIMIT 25""",
                [TASK_TYPE],
            )
            tasks = []
            for (identifier,) in cursor.fetchall():
                guard.check()
                tasks.append(
                    enqueue(
                        task_type=TASK_TYPE,
                        domain_request_id=identifier,
                        actor_id=self.worker_id,
                        correlation_id=uuid4(),
                        idempotency_key=identifier,
                        admit=lambda *args: True,
                    )
                )
            guard.check()
            return tuple(tasks)
