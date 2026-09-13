"""One connection-pinned scheduler, with broker publication outside transactions.

The admitted runtime keeps this dedicated database connection for its lifetime;
it must not use the installer's close-after-each-pass loop. Losing the connection
requires exiting/reacquiring ownership, never silently continuing after reconnect.
Duplicate transport hints remain harmless even across a connection-loss race.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event, get_ident, local

from django.db import connection

from parishkit.stewardship.storage import StorageInvariantError

from .scanning import ScanCursor, collect_hints

SCHEDULER_LOCK = (736229, 1)
_scope = local()


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
    """Check both local connection continuity and actual server-side ownership."""

    def __init__(self, raw):
        self.raw, self.thread = raw, get_ident()

    def check(self):
        """Every scan/publication boundary verifies the same live owning session."""
        if (
            get_ident() != self.thread
            or connection.connection is not self.raw
            or self.raw.closed
            or getattr(_scope, "guard", None) is not self
        ):
            raise SchedulerOwnershipLost("The scheduler session is no longer owned.")
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


def scan_once(guard, *, handlers, publish, cursor=None, limit=100, stop=None):
    """Emit a bounded page of hints; failed delivery remains durable and replayable.

    The runtime transport must impose a finite publication timeout and translate
    known transport failures to HintPublicationUnavailable. Each failed hint
    remains durable for the next sweep, while the cursor advances so one broken
    queue cannot indefinitely block another. Unexpected errors still propagate.
    """
    if not isinstance(guard, SchedulerGuard) or not callable(publish):
        raise ValueError("An owned scheduler and bounded publisher are required.")
    if stop is not None and not isinstance(stop, Event):
        raise ValueError("Scheduler drainage requires a process-owned stop event.")
    guard.check()
    hints, position = collect_hints(handlers=handlers, cursor=cursor, limit=limit)
    published = unconfirmed = 0
    for hint in hints:
        guard.check()
        if stop is not None and stop.is_set():
            return ScanResult(None, published, unconfirmed)
        try:
            publish(hint)
        except HintPublicationUnavailable:
            unconfirmed += 1
        else:
            published += 1
    guard.check()
    return ScanResult(position, published, unconfirmed)
