"""Shared live-task fencing for domain transactions, without queue authorization."""

from dataclasses import dataclass
from uuid import UUID

from django.db import connection

from parishkit.stewardship.storage import StorageInvariantError

from .models import TaskRun


class TaskOwnershipLost(RuntimeError):
    """A worker's exact task claim is missing, expired or has been replaced."""


@dataclass(frozen=True)
class TaskClaim:
    """An internal owner proof; HTTP callers cannot use it to authorize work."""

    run_id: UUID
    fence: int
    worker_id: UUID

    def __post_init__(self):
        """Reject coercions, including True as fencing token 1."""
        if (
            not isinstance(self.run_id, UUID)
            or not isinstance(self.worker_id, UUID)
            or type(self.fence) is not int
            or not 1 <= self.fence < 2**63
        ):
            raise TaskOwnershipLost("An exact task claim is required.")


def database_now():
    """Use PostgreSQL wall time after lock waits, never a transaction-start clock."""
    if connection.vendor != "postgresql" or not connection.in_atomic_block:
        raise StorageInvariantError("Task ownership requires a PostgreSQL transaction.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def lock_task_claim(claim):
    """Lock retry root before execution, matching every TaskRun state mutation."""
    if not isinstance(claim, TaskClaim):
        raise TaskOwnershipLost("An exact task claim is required.")
    database_now()  # Require the caller to retain locks through its domain effect.
    try:
        root_id = TaskRun.objects.values_list("root_id", flat=True).get(pk=claim.run_id)
        if root_id != claim.run_id:
            TaskRun.objects.select_for_update().get(pk=root_id)
        task = TaskRun.objects.select_for_update().get(pk=claim.run_id)
    except TaskRun.DoesNotExist:
        raise TaskOwnershipLost("Task ownership no longer exists.") from None
    if (
        task.state != "running"
        or task.fence != claim.fence
        or task.worker_id != claim.worker_id
        or task.lease_expires_at <= database_now()
    ):
        raise TaskOwnershipLost("Task ownership is no longer current.")
    return task
