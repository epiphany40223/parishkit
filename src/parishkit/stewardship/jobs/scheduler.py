"""One connection-pinned scheduler, with broker publication outside transactions.

The admitted runtime keeps this dedicated database connection for its lifetime;
it must not use the installer's close-after-each-pass loop. Losing the connection
requires exiting/reacquiring ownership, never silently continuing after reconnect.
Duplicate transport hints remain harmless even across a connection-loss race.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event, get_ident, local
from time import monotonic

from django.db import connection

from parishkit.stewardship.storage import StorageInvariantError

from .scanning import ScanCursor, collect_hints

SCHEDULER_LOCK = (736229, 1)
_scope = local()

# How long one server-side ownership confirmation stands (#629). Every check
# still verifies this thread, connection object and open state; only the
# pg_locks query is skipped within this interval of the last one that passed.
PROBE_SECONDS = 1.0


class SchedulerBusy(RuntimeError):
    """Another scheduler owns the PostgreSQL session lock."""


class SchedulerOwnershipLost(RuntimeError):
    """A stopped/disconnected scheduler must not silently regain apparent ownership."""


class HintPublicationUnavailable(RuntimeError):
    """The bounded transport could not confirm publication; durable work remains."""


@dataclass(frozen=True)
class ScanResult:
    """Transport counts are not task outcomes; failed hints recur on the next sweep."""

    cursor: ScanCursor | None
    published: int
    unconfirmed: int


class SchedulerGuard:
    """Check both local connection continuity and actual server-side ownership.

    The local checks (same thread, same open connection object, guard still
    in scope) run on every call. The server-side ``pg_locks`` query confirms
    the lock itself, but reading ``pg_locks`` takes every lock-manager
    partition, and the producers call ``check`` around each step, dozens of
    times in every idle 2-second loop (#629). So a passed query stands for
    ``PROBE_SECONDS``: it runs at most once a second, which still means at
    the start of every loop, since loops are at least 2 seconds apart.

    Within that second a session the server ended (pg_terminate_backend,
    idle_session_timeout, a dropped network) still looks open until its
    next statement fails, so one check can pass. That is safe: database work
    on the dead session fails at its next statement, the SQL guards that
    need scheduler ownership check lock 736229 in the caller's own session,
    and the only exposure is a duplicate hint, which claim_hint() ignores
    under the task's row lock. See the background-processing spec.
    """

    def __init__(self, raw):
        self.raw, self.thread, self.clock = raw, get_ident(), monotonic
        # When the server last confirmed ownership; None forces the query.
        self.confirmed = None

    def check(self):
        """Every scan/publication boundary verifies the same live owning session."""
        if (
            get_ident() != self.thread
            or connection.connection is not self.raw
            or self.raw.closed
            or getattr(_scope, "guard", None) is not self
        ):
            raise SchedulerOwnershipLost("The scheduler session is no longer owned.")
        now = self.clock()
        if self.confirmed is not None and now - self.confirmed < PROBE_SECONDS:
            return
        try:
            with self.raw.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                    "AND pid=pg_backend_pid() AND classid=%s AND objid=%s "
                    "AND objsubid=2 AND granted)",
                    SCHEDULER_LOCK,
                )
                if not cursor.fetchone()[0]:
                    raise SchedulerOwnershipLost(
                        "The scheduler session is no longer owned."
                    )
        except Exception:
            raise SchedulerOwnershipLost(
                "The scheduler session is no longer owned."
            ) from None
        self.confirmed = now


@contextmanager
def scheduler_session():
    """Acquire without waiting and release only this exact connection's lock."""
    if (
        connection.vendor != "postgresql"
        or connection.in_atomic_block
        or not connection.get_autocommit()
        or getattr(_scope, "guard", None)
    ):
        raise StorageInvariantError("Scheduling requires its own PostgreSQL session.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s,%s)", SCHEDULER_LOCK)
        if not cursor.fetchone()[0]:
            raise SchedulerBusy("Another scheduler is already active.")
    raw = connection.connection
    guard = SchedulerGuard(raw)
    _scope.guard = guard
    failed = False
    try:
        yield guard
    except BaseException:
        failed = True
        raise
    finally:
        _scope.guard = None
        try:
            _release(raw)
        except BaseException:
            if not failed:
                raise


def _release(raw):
    """Never create a new connection to release ownership from an old session."""
    if raw.closed:
        if connection.connection is raw:
            connection.close()
        return
    try:
        with raw.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s,%s)", SCHEDULER_LOCK)
            if not cursor.fetchone()[0]:
                raise SchedulerOwnershipLost(
                    "The scheduler session is no longer owned."
                )
    except BaseException:
        if connection.connection is raw:
            connection.close()
        else:
            raw.close()
        raise


def scan_once(
    guard,
    *,
    handlers,
    publish,
    cursor=None,
    limit=100,
    stop=None,
    health=None,
    recent=None,
):
    """Emit a bounded page of hints; failed delivery remains durable and replayable.

    The runtime transport must impose a finite publication timeout and translate
    known transport failures to HintPublicationUnavailable. Each failed hint
    remains durable for the next sweep, while the cursor advances so one broken
    queue cannot indefinitely block another. Unexpected errors still propagate.
    ``recent`` (a RecentHints) remembers each published hint so later scans
    skip re-admitting the unchanged row for a while.
    """
    if not isinstance(guard, SchedulerGuard) or not callable(publish):
        raise ValueError("An owned scheduler and bounded publisher are required.")
    if stop is not None and not isinstance(stop, Event):
        raise ValueError("Scheduler drainage requires a process-owned stop event.")
    guard.check()
    if health is not None and cursor is None:
        health.begin()
    hints, position = collect_hints(
        handlers=handlers, cursor=cursor, limit=limit, health=health, recent=recent
    )
    published = unconfirmed = 0
    for hint in hints:
        guard.check()
        if stop is not None and stop.is_set():
            if health is not None:
                health.reset()
            return ScanResult(None, published, unconfirmed)
        try:
            publish(hint)
        except HintPublicationUnavailable:
            unconfirmed += 1
            if health is not None:
                health.unknown()
        else:
            published += 1
            if recent is not None:
                recent.published(hint.run_id)
    guard.check()
    if health is not None:
        if stop is not None and stop.is_set():
            health.reset()
        else:
            health.finish(guard, complete=position is None)
    return ScanResult(position, published, unconfirmed)
