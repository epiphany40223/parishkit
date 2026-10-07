"""Take a backup now's requests, as the backup worker settles them (ADM-13 PR 3).

An Administrator's request (``jobs.backup_models.BackupRequest``) waits until
the backup-worker profile's request mode, polled from the host's cron every
five minutes, claims it, runs the ordinary backup and records what became of
it. A scheduled run completes a waiting request too. The request's guard
(``schema/migrations/0016_backup_request.sql``) sets every time from the
database clock and admits only forward moves, so these helpers state only
the new state.

A waiting request lapses 30 minutes after the later of its creation and the
last time a poll saw it held by a bulk Family send; a running one counts as
failed ("did not finish") two hours after its claim. ``settle_lapsed``
records both, and ``waiting`` never returns a lapsed request.
"""

from datetime import timedelta

from django.db import connection

# How long a request may wait unclaimed, from its creation or its last hold.
WAITING_LIMIT = timedelta(minutes=30)
# How long a claimed request may run before it counts as not finished.
RUNNING_LIMIT = timedelta(hours=2)
# The failure category of a run that never came back.
DID_NOT_FINISH = "did_not_finish"


def _interval(limit):
    """A limit as a PostgreSQL interval literal's text."""
    return f"{int(limit.total_seconds())} seconds"


def waiting():
    """The oldest request still waiting and not lapsed, or None.

    None during a restore review too: no request may be claimed then, and a
    scheduled backup still runs without one.
    """
    from django.db.models.functions import Coalesce, Greatest, Now

    from .accounts.runtime_models import SystemConfiguration
    from .jobs.backup_models import BackupRequest

    if SystemConfiguration.objects.filter(restore_review_required=True).exists():
        return None
    return (
        BackupRequest.objects.annotate(
            since=Greatest("created_at", Coalesce("held_at", "created_at"))
        )
        .filter(state="waiting", since__gt=Now() - WAITING_LIMIT)
        .order_by("created_at", "id")
        .first()
    )


def settle_lapsed():
    """Expire lapsed waiting requests and fail long-running ones.

    Returns ``(expired, failed)`` counts. Runs under the backup lock, so the
    running request it fails cannot be one this process is running. Each
    kind that settles anything is a limit that stopped work, so it is
    recorded through the timeout log (#287): what (``lease``, the request's
    claim on the server's attention), the limit, the oldest settled
    request's total age (from its creation or last hold, or from its claim)
    and how many requests it settled. Scheduled runs settle too.
    """
    expired = _settle(
        "UPDATE public.stewardship_backup_request SET state='expired' "
        "WHERE state='waiting' AND greatest(created_at,coalesce(held_at,"
        "created_at))<=statement_timestamp()-%s::interval "
        "RETURNING extract(epoch FROM statement_timestamp()-greatest(created_at,"
        "coalesce(held_at,created_at)))",
        WAITING_LIMIT,
    )
    failed = _settle(
        "UPDATE public.stewardship_backup_request SET state='failed',"
        "failure_kind=%s WHERE state='running' "
        "AND claimed_at<=statement_timestamp()-%s::interval "
        "RETURNING extract(epoch FROM statement_timestamp()-claimed_at)",
        RUNNING_LIMIT,
        prefix=[DID_NOT_FINISH],
    )
    return expired, failed


def _settle(statement, limit, *, prefix=()):
    """Run one settling UPDATE; record a timeout when it settled any request."""
    from .audit.timeouts import record_timeout
    from .observability import Event

    with connection.cursor() as cursor:
        cursor.execute(statement, [*prefix, _interval(limit)])
        ages = [row[0] for row in cursor.fetchall()]
    if ages:
        record_timeout(
            Event.TASK_TIMED_OUT,
            what="lease",
            level="WARNING",
            limit_seconds=int(limit.total_seconds()),
            elapsed_seconds=int(max(ages)),
            count=len(ages),
            bind_task=False,
        )
    return len(ages)


def _move(request, **values):
    """Apply one guarded move to ``request``; refuse a request that moved on."""
    from parishkit.config import ConfigError

    from .jobs.backup_models import BackupRequest

    moved = BackupRequest.objects.filter(pk=request.pk, state=request.state).update(
        **values
    )
    if moved != 1:
        raise ConfigError("The backup request changed while it was settled.")
    request.refresh_from_db()


def held(request):
    """Record that a poll saw a bulk Family send hold this request."""
    from django.db.models.functions import Now

    _move(request, held_at=Now())


def claim(request):
    """Mark the request running; the guard records when."""
    _move(request, state="running")


def finish(request, run_id):
    """Mark the request finished, linked to the backup run it produced."""
    _move(request, state="finished", backup_run_id=run_id)


def fail(request, error):
    """Mark the request failed with the category the process log records.

    Never raises: the backup's own failure is what the caller reports.
    """
    from .observability import emit_failure, failure_kind_of

    try:
        _move(request, state="failed", failure_kind=str(failure_kind_of(error)))
    except Exception as settle_error:
        emit_failure(settle_error)
