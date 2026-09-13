"""Short PostgreSQL transactions fence every source mutation and external call.

The returned claim is internal worker state, never an authorization credential
accepted from HTTP. The background admission layer must authorize the TaskRun
before it can become running; these operations additionally check its live
worker, task fence and expiry under locks. Provider work happens only after the
transaction commits. Publication recovery owns ambiguous upstream outcomes.
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.db import connection, transaction

from parishkit.stewardship.jobs.ownership import (
    TaskClaim,
    TaskOwnershipLost,
    database_now,
    lock_task_claim,
)
from parishkit.stewardship.storage import StorageInvariantError

from .models import SourceMutationLease


class SourceLeaseUnavailable(RuntimeError):
    """Another owner or its outstanding request safety window prevents work."""


class SourceFenceLost(RuntimeError):
    """The task or source ownership proof is no longer current."""


@dataclass(frozen=True)
class SourceClaim:
    """Both fences and the exact worker must match at every protected operation."""

    task_id: UUID
    task_fence: int
    worker_id: UUID
    fence: int
    phase: str

    def __post_init__(self):
        """Do not let boolean/coerced source fences masquerade as exact ownership."""
        try:
            TaskClaim(self.task_id, self.task_fence, self.worker_id)
        except TaskOwnershipLost:
            raise SourceFenceLost("An exact source claim is required.") from None
        if (
            type(self.fence) is not int
            or not 1 <= self.fence < 2**63
            or self.phase not in {"full", "delta", "publication", "compaction"}
        ):
            raise SourceFenceLost("An exact source claim is required.")


def _duration(value, *, maximum=3600):
    """Bound configuration errors without coercing booleans or fractional values."""
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("Source lease durations must be bounded positive seconds.")
    return timedelta(seconds=value)


def _now():
    """Use the database wall clock, including after lock acquisition waits."""
    return database_now()


def _task(task_id, task_fence, worker_id):
    """Acquire the common task-before-source lock order and validate live ownership."""
    try:
        return lock_task_claim(TaskClaim(task_id, task_fence, worker_id))
    except TaskOwnershipLost:
        raise SourceFenceLost("Source task ownership is no longer current.") from None


def _lease():
    """The migration seeds this row; missing durable ownership fails closed."""
    try:
        return SourceMutationLease.objects.select_for_update().get(singleton=True)
    except SourceMutationLease.DoesNotExist:
        raise StorageInvariantError(
            "Source ownership has not been initialized."
        ) from None


def _owned(claim):
    """Return locked ownership only while both independent fences remain live."""
    if not isinstance(claim, SourceClaim):
        raise SourceFenceLost("A source claim is required.")
    task = _task(claim.task_id, claim.task_fence, claim.worker_id)
    lease = _lease()
    now = _now()
    if (
        task.lease_expires_at <= now
        or lease.owner_id != claim.task_id
        or lease.worker_id != claim.worker_id
        or lease.task_fence != claim.task_fence
        or lease.fence != claim.fence
        or lease.phase != claim.phase
        or lease.expires_at is None
        or lease.expires_at <= now
    ):
        raise SourceFenceLost("Source mutation ownership is no longer current.")
    return lease, now


def _save(lease, worker_id):
    """Advance the database-enforced version for every ownership operation."""
    lease.version += 1
    lease.actor_id = worker_id
    lease.save()


def acquire_source(*, task_id, task_fence, worker_id, phase, lease_seconds=90):
    """Claim idle or safely expired ownership, never overlap a prior request."""
    duration = _duration(lease_seconds)
    if phase not in {"full", "delta", "publication", "compaction"}:
        raise ValueError("Unknown source mutation phase.")
    with transaction.atomic():
        task = _task(task_id, task_fence, worker_id)
        lease = _lease()
        now = _now()
        if task.lease_expires_at <= now:
            raise SourceFenceLost("Source task expired while waiting for ownership.")
        if any(
            deadline is not None and deadline > now
            for deadline in (lease.expires_at, lease.external_deadline)
        ):
            raise SourceLeaseUnavailable(
                "Source mutation is already owned or draining."
            )
        lease.owner_id = task_id
        lease.worker_id = worker_id
        lease.task_fence = task_fence
        lease.fence += 1
        lease.phase = phase
        lease.acquired_at = lease.heartbeat_at = now
        lease.expires_at = now + duration
        lease.external_deadline = None
        _save(lease, worker_id)
        return SourceClaim(task_id, task_fence, worker_id, lease.fence, phase)


def renew_source(claim, *, lease_seconds=90):
    """Heartbeat without reviving an expired lease or replacing its fencing token."""
    duration = _duration(lease_seconds)
    with transaction.atomic():
        lease, now = _owned(claim)
        lease.heartbeat_at = now
        lease.expires_at = max(lease.expires_at, now + duration)
        _save(lease, claim.worker_id)


def reserve_source_request(claim, *, timeout_seconds, safety_seconds=15):
    """Fence immediately before a bounded provider call and persist safe takeover.

    The adapter must use the supplied request timeout. The deadline includes a
    safety margin and survives failure/release; it is not evidence that an
    ambiguous upstream publication was undone. No network call occurs here.
    """
    timeout = _duration(timeout_seconds, maximum=300)
    safety = _duration(safety_seconds, maximum=300)
    with transaction.atomic():
        lease, now = _owned(claim)
        deadline = now + timeout + safety
        lease.external_deadline = max(lease.external_deadline or deadline, deadline)
        _save(lease, claim.worker_id)
        return lease.external_deadline


def release_source(claim):
    """Release live ownership while preserving any external-call safety deadline."""
    with transaction.atomic():
        lease, _ = _owned(claim)
        lease.owner_id = lease.worker_id = lease.expires_at = None
        lease.phase = "idle"
        _save(lease, claim.worker_id)


def verify_source(claim):
    """Pin the live fence through a caller's atomic promotion transaction."""
    if not connection.in_atomic_block:
        raise StorageInvariantError("Source promotion requires an outer transaction.")
    return _owned(claim)[0]
