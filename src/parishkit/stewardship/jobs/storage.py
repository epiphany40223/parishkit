"""Internal task metadata transactions, not an operational worker API.

Every operation requires an owning-service admission callback, including repeat
commands. It executes under the retry-root lock and must recheck current policy
and task-specific safety/completion evidence without doing external work. BG-01
supplies real admission/reconciliation and queue consumers in its later phase.
None exists here; callers must not infer permission from a UUID, fence or action.
"""

import hashlib
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from django.db import connection, transaction
from django.db.models.functions import Now

from parishkit.stewardship.observability import correlation
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .models import TaskRun


@dataclass(frozen=True)
class TaskStatus:
    """An immutable callback/status view without mutable ORM state or task payloads."""

    run_id: UUID
    root_id: UUID
    task_type: str
    domain_request_id: UUID | None
    state: str
    version: int
    attempt: int
    fence: int
    worker_id: UUID | None
    parent_id: UUID | None
    retry_sequence: int


def _status(run):
    """Freeze only identifiers and concurrency state for admission and receipts."""
    return TaskStatus(
        run.pk,
        run.root_id,
        run.task_type,
        run.domain_request_id,
        run.state,
        run.version,
        run.attempt,
        run.fence,
        run.worker_id,
        run.parent_id,
        run.retry_sequence,
    )


def _uuid(value, *, optional=False):
    """Exclude private arbitrary text from identifier errors."""
    if not (isinstance(value, UUID) or (optional and value is None)):
        raise TypeError("Task identifiers must be UUIDs.")


def _admit(callback, action, status):
    """Require the owning verifier, which must raise on any failed prerequisite."""
    if not callable(callback):
        raise TypeError("Task admission callback is required.")
    callback(action, status)


@contextmanager
def _locked(correlation_id, *, root_id=None, enqueue_key=None):
    """Keep root claims short; callers can compose domain updates in an outer tx.

    Keyed root inserts have no row to lock: a key-scoped advisory lock serializes
    their allocation. Unkeyed inserts need no lock; existing chains lock roots.
    External side effects must never occur while this transaction is held.
    """
    if connection.vendor != "postgresql":
        raise StorageInvariantError("Task storage requires PostgreSQL.")
    with correlation(correlation_id), transaction.atomic():
        if enqueue_key is not None:
            # Hash collisions only serialize unrelated allocations; the database
            # unique constraint, not this shortened hash, identifies executions.
            lock_key = int.from_bytes(
                hashlib.sha256(enqueue_key.encode()).digest()[:4], signed=True
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s, %s)", [736214, lock_key]
                )
        if root_id is not None:
            TaskRun.objects.select_for_update().get(pk=root_id)
        yield


def enqueue(
    *,
    task_type,
    domain_request_id,
    actor_id,
    correlation_id,
    admit,
    idempotency_key=None,
):
    """Create an internal root, or return the exact previously bound execution.

    Keys are opaque UUIDs; domain consumers map their semantic identity to these
    keys and retain their own fulfillment constraints. Without a key, every call
    represents separately authorized new work, not a retry of a failed run.
    """
    if (
        type(task_type) is not str
        or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", task_type) is None
    ):
        raise ValueError("Invalid internal task type.")
    for value in (domain_request_id, actor_id, idempotency_key):
        _uuid(value, optional=True)
    _uuid(correlation_id)
    with _locked(
        correlation_id,
        enqueue_key=None
        if idempotency_key is None
        else f"{task_type}:{idempotency_key}",
    ):
        existing = None
        if idempotency_key is not None:
            existing = (
                TaskRun.objects.select_for_update()
                .filter(task_type=task_type, idempotency_key=str(idempotency_key))
                .first()
            )
        if existing is not None:
            if (
                existing.domain_request_id != domain_request_id
                or existing.initiated_by_id != actor_id
            ):
                raise ValueError("Task execution key is already bound.")
            _admit(admit, "enqueue", _status(existing))
            return _status(existing)
        identifier = uuid4()
        run = TaskRun(
            id=identifier,
            root_id=identifier,
            task_type=task_type,
            domain_request_id=domain_request_id,
            initiated_by_id=actor_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
            idempotency_key=None if idempotency_key is None else str(idempotency_key),
        )
        _admit(admit, "enqueue", _status(run))
        run.save(force_insert=True)
        return _status(run)


def retry_failed(*, run_id, command_id, actor_id, correlation_id, admit):
    """Deduplicate an explicit retry command under the immutable retry-root lock."""
    for value in (run_id, command_id, actor_id, correlation_id):
        _uuid(value)
    original = TaskRun.objects.get(pk=run_id)
    with _locked(correlation_id, root_id=original.root_id):
        original.refresh_from_db()
        existing = TaskRun.objects.filter(
            root_id=original.root_id, retry_command_id=command_id
        ).first()
        if existing is not None:
            if existing.parent_id != run_id or existing.initiated_by_id != actor_id:
                raise ValueError("Task retry command is already bound.")
            _admit(admit, "explicit_retry_replay", _status(existing))
            return _status(existing)
        latest = (
            TaskRun.objects.filter(root_id=original.root_id)
            .order_by("-retry_sequence")
            .first()
        )
        if latest.pk != run_id or original.state != "failed":
            raise StorageInvariantError("Only the latest failed run can be retried.")
        _admit(admit, "explicit_retry", _status(original))
        run = TaskRun(
            root_id=original.root_id,
            parent_id=run_id,
            retry_sequence=original.retry_sequence + 1,
            retry_command_id=command_id,
            task_type=original.task_type,
            domain_request_id=original.domain_request_id,
            initiated_by_id=actor_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
            action="explicit_retry",
            idempotency_key=f"retry:{original.root_id}:{original.retry_sequence + 1}",
        )
        run.save(force_insert=True)
        return _status(run)


def change_run(
    *,
    run_id,
    action,
    expected_version,
    actor_id,
    correlation_id,
    admit,
    fence=None,
    lease_seconds=None,
    retry_seconds=None,
    progress=None,
):
    """Apply one fenced metadata transition after domain-specific verification.

    For worker writes actor_id is the claimed worker UUID. Heartbeat/claim accept
    1..300-second leases; retry/recovery waits accept 1..86,400 seconds. These are
    storage bounds, not scheduler defaults. Expiry is only abandonment, never proof
    of failed external work. Recovery outcomes require the owning callback's proof.
    """
    _uuid(run_id)
    _uuid(actor_id, optional=True)
    _uuid(correlation_id)
    actions = {
        "claim": "running",
        "heartbeat": "running",
        "progress": "running",
        "complete": "succeeded",
        "retryable_failure": "retry_wait",
        "permanent_failure": "failed",
        "safe_cancel": "cancelled",
        "lease_expired": "abandoned",
        "recovery_retry": "retry_wait",
        "recovery_complete": "succeeded",
        "recovery_fail": "failed",
        "recovery_cancel": "cancelled",
    }
    if type(action) is not str or action not in actions:
        raise ValueError("Unknown task action.")
    if action == "claim":
        _uuid(actor_id)
    elif action == "lease_expired" and actor_id is not None:
        raise ValueError("Lease expiry has no fabricated worker actor.")
    if type(expected_version) is not int or expected_version < 1:
        raise ValueError("A positive task version is required.")
    for value, needed, maximum in (
        (lease_seconds, action in ("claim", "heartbeat"), 300),
        (retry_seconds, action in ("retryable_failure", "recovery_retry"), 86400),
    ):
        if (needed and (type(value) is not int or not 1 <= value <= maximum)) or (
            not needed and value is not None
        ):
            raise ValueError("Invalid task timing parameters.")
    if action == "progress":
        if (
            type(progress) is not tuple
            or len(progress) != 2
            or any(
                type(value) is not int or not 0 <= value <= 2**63 - 1
                for value in progress
            )
            or progress[0] > progress[1]
        ):
            raise ValueError("Invalid task progress.")
    elif progress is not None:
        raise ValueError("Unexpected task progress.")
    original = TaskRun.objects.get(pk=run_id)
    with _locked(correlation_id, root_id=original.root_id):
        run = TaskRun.objects.select_for_update().get(pk=run_id)
        _admit(admit, action, _status(run))
        if run.version != expected_version:
            raise StaleRecordError("The task changed; reload before retrying.")
        if run.state == "running" and action != "lease_expired":
            if (
                type(fence) is not int
                or fence != run.fence
                or actor_id != run.worker_id
            ):
                raise StaleRecordError("The task claim is no longer owned.")
        elif fence is not None:
            raise ValueError("Unexpected task fencing token.")
        if action == "claim":
            run.worker_id = actor_id
            run.fence += 1
            run.attempt += 1
            run.progress_current = run.progress_total = 0
        elif action == "lease_expired":
            run.fence += 1
        if action in ("claim", "heartbeat"):
            # Expressions share the UPDATE statement's clock with the SQL guard;
            # a separate clock-read round trip could consume the whole lease.
            run.heartbeat_at = Now()
            run.lease_expires_at = Now() + timedelta(seconds=lease_seconds)
        elif actions[action] != "running":
            run.lease_expires_at = None
        if retry_seconds is not None:
            run.not_before = Now() + timedelta(seconds=retry_seconds)
        if progress is not None:
            run.progress_current, run.progress_total = progress
        run.state, run.action = actions[action], action
        run.actor_id, run.correlation_id = actor_id, correlation_id
        run.version += 1
        run.save()
        return _status(run)
