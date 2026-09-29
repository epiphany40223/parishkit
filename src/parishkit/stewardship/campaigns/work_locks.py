"""Common short-transaction order for lifecycle, task and source admission.

For campaign-scoped work the existing lifecycle advisory lock precedes
retry-root/task and domain rows. Unrelated tasks retain independent root locks.
This prevents a task callback waiting for Campaign while an archive/configuration
transition waits for that same task. File installers retain their separate
session lock before joining this order. The one-time setup completion worker
also pins installer serialization, without writing files, before entering its
short atomic SQL activation. No worker holds this transaction during provider I/O.
"""

from contextlib import contextmanager
from uuid import UUID

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


def lock_campaign_exports(campaign_id):
    """Exclude one campaign's export admission from this transition (#147).

    Export admission no longer joins the global work order: it takes only a
    per-campaign lock. A transition that changes what export admission reads
    for a campaign (its lifecycle, a purge work gate, the go-live gate) takes
    the same lock here, after the global lock and before any row lock, so it
    waits for exports already admitted there and later admissions see it.
    The SQL helper joins the global order itself when a caller has not.
    """
    if not isinstance(campaign_id, UUID):
        raise TypeError("Campaign identities must be UUIDs.")
    if connection.vendor != "postgresql" or not connection.in_atomic_block:
        raise StorageInvariantError("Work admission requires a PostgreSQL transaction.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_export_campaign_lock_v1(%s,true)", [campaign_id]
        )


def lock_current_campaign_exports():
    """Order a configuration activation with the current campaign's exports.

    An activation can change the current campaign's end date or reopen it
    (a lifecycle transition), so it takes that campaign's export lock right
    after the global lock and before any row lock (#147). The current-campaign
    pointer changes only under the global lock the caller already holds.
    """
    if connection.vendor != "postgresql" or not connection.in_atomic_block:
        raise StorageInvariantError("Work admission requires a PostgreSQL transaction.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_export_campaign_lock_v1(current_campaign_id,true) "
            "FROM stewardship_system_configuration "
            "WHERE current_campaign_id IS NOT NULL"
        )


@contextmanager
def export_transaction(campaign_id):
    """Admit one campaign's export without waiting on the global work order.

    Queueing an export used to take the global lock, so it waited behind every
    source promotion, installer and task transition (#147). It needs only to
    be ordered against transitions of its own campaign: the per-campaign lock
    comes first, before any row lock, and the SQL admission guards take it too.
    Inside a transaction that already holds the global lock, which already
    excludes every transition, the SQL helper takes nothing more.
    """
    if not isinstance(campaign_id, UUID):
        raise TypeError("Campaign identities must be UUIDs.")
    if connection.vendor != "postgresql":
        raise StorageInvariantError("Export admission requires PostgreSQL.")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_export_campaign_lock_v1(%s,false)", [campaign_id]
            )
        yield


@contextmanager
def work_transaction():
    """Compose one ordered short transaction, including inside an existing owner."""
    with transaction.atomic():
        lock_work_order()
        yield


@contextmanager
def read_transaction():
    """Observe one consistent snapshot without joining the writers' lock order.

    The work-order lock orders writers so they cannot deadlock; a pure read
    takes no row locks and needs only a coherent view of rows that writers
    commit atomically (an activated configuration with its projections, a
    promoted source snapshot with its pointer). REPEATABLE READ gives every
    statement in the block the same snapshot, so a read can never pair a newly
    committed configuration with an older projection, and READ ONLY makes any
    accidental write fail closed instead of escaping the writers' order. Admin
    page views use this so a long writer (a source promotion, an installer)
    never blocks them. Helpers that assert require_work_order() still refuse
    here: such a view needs work_transaction().

    Inside an existing transaction the snapshot and isolation already belong
    to its owner, so the block only nests; it never adds or drops that owner's
    lock.
    """
    if connection.vendor != "postgresql":
        raise StorageInvariantError("Snapshot reads require PostgreSQL.")
    if connection.in_atomic_block:
        with transaction.atomic():
            yield
        return
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        yield
