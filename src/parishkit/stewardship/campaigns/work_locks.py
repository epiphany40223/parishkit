"""Common short-transaction order for lifecycle, task and source admission.

For campaign-scoped work the existing lifecycle advisory lock precedes
retry-root/task and domain rows. Unrelated tasks retain independent root locks.
This prevents a task callback waiting for Campaign while an archive/configuration
transition waits for that same task. File installers retain their separate
session lock before joining this order; workers never acquire the file lock or
hold this transaction while waiting for external services.
"""

from contextlib import contextmanager

from django.db import connection, transaction

from parishkit.stewardship.storage import StorageInvariantError

WORK_ORDER_LOCK = (736220, 1)


def lock_work_order():
    """Join lifecycle serialization before taking any task/domain write locks."""
    if connection.vendor != "postgresql" or not connection.in_atomic_block:
        raise StorageInvariantError("Work admission requires a PostgreSQL transaction.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s,%s)", WORK_ORDER_LOCK)


def require_work_order():
    """Fail closed if a caller skipped the owning scope before taking task locks."""
    if connection.vendor != "postgresql" or not connection.in_atomic_block:
        raise StorageInvariantError("Work admission requires an ordered transaction.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
            "AND locktype='advisory' AND classid=%s AND objid=%s AND objsubid=2 "
            "AND mode='ExclusiveLock' AND granted)",
            WORK_ORDER_LOCK,
        )
        if cursor.fetchone() != (True,):
            raise StorageInvariantError(
                "Work admission requires its owning lock order."
            )


@contextmanager
def work_transaction():
    """Compose one ordered short transaction, including inside an existing owner."""
    with transaction.atomic():
        lock_work_order()
        yield
