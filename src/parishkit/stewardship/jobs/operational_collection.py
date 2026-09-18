"""Bounded, fenced intake of critical logs from Python and SQL domain producers."""

from uuid import UUID, uuid5

from django.db import connection, transaction

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.campaigns.work_locks import (
    require_work_order,
    work_transaction,
)
from parishkit.stewardship.observability import Event, correlation
from parishkit.stewardship.source.health import (
    admitted_source_scope,
    observe_source_health,
)
from parishkit.stewardship.storage import StorageInvariantError

from .dispatch import Handler, RecoveryPlan
from .mail_health import needs_mail_observation, observe_mail_health
from .models import TaskRun
from .operational_models import OperationalLogReceipt
from .operational_sources import critical_log
from .ownership import database_now
from .phases import TaskPhase
from .queues import WorkQueue
from .scheduler import SchedulerGuard
from .storage import enqueue

TASK_TYPE = "operational_collect"
NAMESPACE = UUID("66b36d0c-07e5-4f86-956e-91e589973b36")
BATCH_SIZE = 50


def pending_logs():
    """Anti-join exact receipts, never a time cursor that skips late commits."""
    return OperationalLog.objects.filter(level="CRITICAL").exclude(
        pk__in=OperationalLogReceipt.objects.values("log_id")
    )


def produce_collection(guard):
    """At most one new bounded intake task per minute, independent of campaign gates."""
    if not isinstance(guard, SchedulerGuard):
        raise PermissionError("Operational intake requires the owned scheduler.")
    guard.check()
    with work_transaction():
        if (
            not pending_logs().exists()
            and admitted_source_scope() is None
            and not needs_mail_observation()
        ):
            return ()
        key = uuid5(NAMESPACE, str(int(database_now().timestamp()) // 60))
        task = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=key,
            idempotency_key=key,
            actor_id=None,
            correlation_id=key,
            admit=admit_collection,
        )
        return (task.run_id,)


def _current(status):
    """A task hint never supplies its own type, progress or current fencing proof."""
    require_work_order()
    if status.task_type != TASK_TYPE:
        raise PermissionError("Operational intake task type differs.")
    return TaskRun.objects.get(
        pk=status.run_id,
        root_id=status.root_id,
        task_type=TASK_TYPE,
        domain_request_id=status.domain_request_id,
        state=status.state,
        version=status.version,
        fence=status.fence,
    )


def _completed(row):
    """A bounded batch is complete only with its retained receipts/progress."""
    if (
        row.phase != TaskPhase.VERIFYING.value
        or row.progress_current != row.progress_total
    ):
        return False
    return OperationalLogReceipt.objects.filter(run=row).count() == row.progress_total


def recovery_collection(status):
    """Committed intake is repeat-safe; an interrupted transaction left no receipts."""
    row = _current(status)
    if row.state != "abandoned":
        return None
    if _completed(row):
        return RecoveryPlan("recovery_complete")
    if row.attempt >= 3:
        return RecoveryPlan("recovery_fail")
    return RecoveryPlan("recovery_retry", 30)


def admit_collection(action, status):
    """Operational intake ignores campaign modes, but retains exact Task ownership."""
    require_work_order()
    if status.task_type != TASK_TYPE or not isinstance(status.domain_request_id, UUID):
        return False
    if action == "enqueue":
        return True
    row = _current(status)
    if action == "lease_expired" or action == "heartbeat":
        return True
    if action == "complete":
        return _completed(row)
    if action == "recovery_hint" and row.state == "running":
        return True
    if action.startswith("recovery_"):
        plan = recovery_collection(status)
        return plan is not None and (action == "recovery_hint" or plan.action == action)
    return action in {"hint", "claim", "effect", "progress"}


def _execute(execution):
    """Persist one safe page and its acknowledgements under the live execution fence."""
    if connection.in_atomic_block or not execution.control.active:
        raise StorageInvariantError("Operational intake requires maintained ownership.")
    with execution.effect():
        # Work-order serialization excludes another collector while reading the
        # absent receipts. The producer's log remains immutable and independently
        # durable; notice failure rolls back this entire page, not the source log.
        page = list(
            pending_logs()
            .only("id", "event", "correlation_id")
            .order_by("created_at", "pk")[:BATCH_SIZE]
        )
        for record in page:
            execution.check()
            with correlation(record.correlation_id):
                incident = critical_log(Event(record.event))
                OperationalLogReceipt.objects.create(
                    log=record,
                    incident=incident,
                    incident_version=incident.version,
                    run_id=execution.claim.run_id,
                    fence=execution.claim.fence,
                    worker_id=execution.claim.worker_id,
                    actor_id=execution.claim.worker_id,
                )
        try:
            # A source-specific defect must not roll back unrelated alert intake.
            # Failed samples retain no partial health effects; the next collector
            # receives only this safe diagnostic, never the exception's values.
            with transaction.atomic():
                observe_source_health()
        except Exception:
            execution.check()
            operational(Event.SOURCE_INVALID, level="CRITICAL")
        try:
            with transaction.atomic():
                observe_mail_health()
        except Exception:
            execution.check()
            # A monitoring defect must not recursively generate mail alerts.
            operational(Event.TASK_FAILED, level="ERROR")
        execution.progress(len(page), len(page), phase=TaskPhase.VERIFYING)
        execution.transition("complete")


def collection_handler(*, scheduler=False):
    """Schedulers only allocate/recover opaque work; workers consume safe metadata."""
    if type(scheduler) is not bool:
        raise TypeError("Operational intake requires a compiled role.")

    def unavailable(execution):
        """A metadata-only scheduler cannot impersonate an incident consumer."""
        raise PermissionError("The scheduler cannot consume critical logs.")

    return Handler(
        WorkQueue.GENERAL,
        admit_collection,
        unavailable if scheduler else _execute,
        recover=recovery_collection,
        scope=work_transaction,
    )
