"""Pinned, response-lifetime campaign reads and deployment-wide download slots.

Only synchronous callers on the owning thread may consume a guarded response.
The response adapter must call close on disconnect and provide an abort hook
that terminates its transport on deadline. No view is exposed by this module.
"""

import hashlib
from contextlib import ExitStack
from dataclasses import dataclass
from threading import BoundedSemaphore, Event, Lock, Timer, get_ident
from time import monotonic
from uuid import UUID

from django.db import connections, transaction
from psycopg import Error as DriverError

from parishkit.stewardship.storage import StorageInvariantError

READ_NAMESPACE = 736221
DOWNLOAD_NAMESPACE = 736222


class ReadUnavailable(RuntimeError):
    """Admission, connection continuity or the total response lifetime failed."""


class DownloadBusy(ReadUnavailable):
    """Map to accessible HTTP 503 with Retry-After: 5 before sending any bytes."""

    status_code = 503
    retry_after = 5


class DownloadConfigurationUnavailable(DownloadBusy):
    """A coordinated capacity change needs matching deployment configuration."""


@dataclass(frozen=True)
class ReadLimits:
    """Finite lifetime and capacity bounds; OPS-04 owns whole-deployment budgeting."""

    interactive_seconds: int = 60
    download_seconds: int = 300
    lock_seconds: int = 5
    download_idle_seconds: int = 330
    drain_seconds: int = 360
    download_capacity: int = 4
    process_pool_size: int = 4

    def __post_init__(self):
        """Reject bools, infinities and budgets that defeat timeout/drain ordering."""
        maxima = {
            "interactive_seconds": 120,
            "download_seconds": 900,
            "lock_seconds": 30,
            "download_idle_seconds": 1200,
            "drain_seconds": 1800,
            "download_capacity": 32,
            "process_pool_size": 32,
        }
        if any(
            type(getattr(self, key)) is not int
            or not 1 <= getattr(self, key) <= maximum
            for key, maximum in maxima.items()
        ):
            raise ValueError("Read limits require bounded positive integers.")
        if not (
            self.lock_seconds < min(self.interactive_seconds, self.download_seconds)
            and self.download_seconds < self.download_idle_seconds < self.drain_seconds
            and self.interactive_seconds < self.drain_seconds
            and self.process_pool_size <= self.download_capacity
        ):
            raise ValueError("Read lifetime and capacity budgets are inconsistent.")


def campaign_lock_key(identifier):
    """Stable signed PostgreSQL key; collisions only serialize unrelated readers."""
    if not isinstance(identifier, UUID):
        raise TypeError("Campaign identifiers must be UUIDs.")
    return int.from_bytes(hashlib.sha256(identifier.bytes).digest()[:4], signed=True)


DEFAULT_LIMITS = ReadLimits()


class DownloadPool:
    """Bounded dedicated, zero-idle connection pool plus PostgreSQL-wide admission.

    Every process/replica has its own explicitly budgeted pool, but session locks
    on the same database enforce the shared cap. No expiry can reissue a live
    slot. A failed global claim closes its dedicated connection immediately.
    """

    def __init__(self, limits=DEFAULT_LIMITS):
        self.limits = limits
        self._slots = BoundedSemaphore(limits.process_pool_size)

    def acquire(self):
        """Never wait for local capacity or borrow another connection class."""
        if not self._slots.acquire(blocking=False):
            raise DownloadBusy("Downloads are busy; please retry shortly.")

    def release(self):
        """Called only after response and owning transaction/connection closure."""
        self._slots.release()


class CampaignReadGuard:
    """Own shared guards, fresh admission, database continuity and total deadline.

    A dedicated download temporarily binds Django's thread-local default alias
    to its pool connection, so ORM queries, callbacks and lazy serialization all
    use the same guarded transaction. Nested/other-thread use is forbidden.
    """

    def __init__(
        self, campaigns, *, authorize, abort, pool=None, limits=DEFAULT_LIMITS
    ):
        identifiers = tuple(campaigns)
        if not identifiers or any(not isinstance(value, UUID) for value in identifiers):
            raise TypeError("At least one canonical campaign identifier is required.")
        if not callable(authorize) or not callable(abort):
            raise TypeError(
                "Fresh authorization and response-abort callbacks are required."
            )
        self.campaigns = tuple(sorted(set(identifiers)))
        self.authorize, self.abort, self.pool = authorize, abort, pool
        self.limits = pool.limits if pool else limits
        self.closed = Event()
        self.expired = Event()
        self._stack = None
        self._timer = None
        self._raw = None
        self._slot_lock = Lock()
        self._slot_owned = False

    def __enter__(self):
        """Acquire capacity before a read transaction, file open or sensitive query."""
        if self._stack is not None or self.closed.is_set():
            raise StorageInvariantError("A read guard cannot be reused.")
        self.thread = get_ident()
        self.original = connections["default"]
        if self.original.vendor != "postgresql" or self.original.in_atomic_block:
            raise StorageInvariantError(
                "A campaign read must own its PostgreSQL transaction."
            )
        self._stack = ExitStack()
        lifetime = (
            self.limits.download_seconds
            if self.pool
            else self.limits.interactive_seconds
        )
        self.deadline = monotonic() + lifetime
        try:
            if self.pool:
                self.pool.acquire()
                self._slot_owned = True
                self._stack.callback(self._release_slot)
                self.db = self.original.copy(alias="default")
                self._stack.callback(self._restore_alias)
                connections["default"] = self.db
                self._stack.callback(self.db.close)
                self.db.ensure_connection()
                self._claim_download()
            else:
                self.db = self.original
                self.db.ensure_connection()
            self._raw = self.db.connection
            remaining = self.deadline - monotonic()
            if remaining <= 0:
                raise ReadUnavailable("Read admission exceeded its response budget.")
            self._stack.enter_context(transaction.atomic(durable=True))
            with self.db.cursor() as cursor:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY"
                )
                for name, seconds in (
                    ("lock_timeout", self.limits.lock_seconds),
                    ("statement_timeout", lifetime),
                    ("transaction_timeout", lifetime),
                    (
                        "idle_in_transaction_session_timeout",
                        self.limits.download_idle_seconds
                        if self.pool
                        else lifetime + 5,
                    ),
                ):
                    cursor.execute(
                        "SELECT set_config(%s, %s, true)", [name, str(seconds * 1000)]
                    )
            self._timer = Timer(remaining, self._expire)
            self._timer.daemon = True
            self._timer.start()
            with self.db.cursor() as cursor:
                for identifier in self.campaigns:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock_shared(%s, %s)",
                        [READ_NAMESPACE, campaign_lock_key(identifier)],
                    )
                cursor.execute(
                    "SELECT id, state FROM stewardship_campaign WHERE id = ANY(%s)",
                    [list(self.campaigns)],
                )
                rows = cursor.fetchall()
                if len(rows) != len(self.campaigns) or any(
                    state in {"purging", "purge_cleanup_failed", "purged"}
                    for _, state in rows
                ):
                    raise ReadUnavailable("Campaign information is unavailable.")
            self.authorize(self)
            self.check()
            return self
        except BaseException:
            self.close()
            raise

    def _claim_download(self):
        """Claim a globally coordinated slot before the guarded transaction."""
        with transaction.atomic(), self.db.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '1s'")
            cursor.execute(
                "SELECT capacity FROM stewardship_download_policy WHERE id=1 FOR SHARE"
            )
            row = cursor.fetchone()
            if row is None or row[0] != self.limits.download_capacity:
                raise DownloadConfigurationUnavailable(
                    "Download capacity configuration requires reconciliation."
                )
            for slot in range(row[0]):
                cursor.execute(
                    "SELECT pg_try_advisory_lock(%s, %s)", [DOWNLOAD_NAMESPACE, slot]
                )
                if cursor.fetchone()[0]:
                    return
        raise DownloadBusy("Downloads are busy; please retry shortly.")

    def _restore_alias(self):
        """Restore the interactive connection only on the owning thread."""
        connections["default"] = self.original

    def _release_slot(self):
        """Deadline and owner cleanup race; return local capacity at most once."""
        with self._slot_lock:
            if self._slot_owned:
                self.pool.release()
                self._slot_owned = False

    def _expire(self):
        """Hard deadline; the adapter must stop transport AND producer before return."""
        self.expired.set()
        stopped = False
        try:
            self.abort()
            stopped = True
        finally:
            try:
                # Raw handles, unlike Django wrappers, may be closed here.
                if self._raw is not None:
                    try:
                        self._raw.cancel()
                    except DriverError:
                        # Cancellation can fail on an already-lost backend; the
                        # unconditional close still tears down this exact handle.
                        pass
                    finally:
                        self._raw.close()
            finally:
                # Failed abort does not prove the producer stopped. A successful
                # abort plus closed connection permits exactly one slot release.
                if stopped and (self._raw is None or self._raw.closed):
                    self._release_slot()

    def check(self):
        """No reconnect, thread handoff, expired response or unguarded continuation."""
        if (
            get_ident() != self.thread
            or self.closed.is_set()
            or self.expired.is_set()
            or monotonic() >= self.deadline
            or self.db.connection is not self._raw
            or self._raw.closed
            or connections["default"] is not self.db
        ):
            raise ReadUnavailable("The guarded response is no longer available.")
        # Detect a server-side disconnect even if the driver has not read EOF yet.
        try:
            with self._raw.cursor() as cursor:
                cursor.execute("SELECT 1")
        except DriverError:
            raise ReadUnavailable("The guarded database connection was lost.") from None

    def close(self):
        """Close on the owning thread; release pool capacity after SQL cleanup."""
        if get_ident() != self.thread:
            raise StorageInvariantError("Close the read guard on its owning thread.")
        if self.closed.is_set():
            return
        self.closed.set()
        if self._timer:
            self._timer.cancel()
        if self._stack:
            if self._raw is not None and self._raw.closed and self.db.in_atomic_block:
                # The timer may close the raw handle, but only this owning
                # thread may update Django's transaction bookkeeping. Without
                # this marker Atomic.__exit__ can reconnect to set autocommit.
                self.db.closed_in_transaction = True
                self.db.needs_rollback = True
            # Always roll back a read transaction, including a cancelled raw
            # connection; never attempt a successful commit after expiration.
            self._stack.__exit__(ReadUnavailable, ReadUnavailable(), None)

    def __exit__(self, *error):
        """Read-only transactions have no effects to commit on response failure."""
        self.close()


class GuardedResponse:
    """Keep lazy rendering/streaming inside its guard until close or exhaustion."""

    def __init__(self, guard, open_content):
        self.guard = guard
        self.source = None
        try:
            guard.__enter__()
            self.source = iter(open_content())
        except BaseException:
            self.close()
            raise

    def __iter__(self):
        return self

    def __next__(self):
        """Check before reading and before emitting any newly produced bytes."""
        try:
            self.guard.check()
            value = next(self.source)
            self.guard.check()
            return value
        except BaseException:
            self.close()
            raise

    def close(self):
        """Transport disconnect hooks must call this even if iteration never starts."""
        try:
            closer = getattr(self.source, "close", None)
            if closer:
                closer()
        finally:
            if self.guard._stack is not None:
                self.guard.close()


def acquire_campaign_drain(campaigns, *, limits=DEFAULT_LIMITS):
    """Acquire ordered exclusive read barriers inside the destructive owner's tx.

    DAT-09/BG-11 must close admission first, then call this before any deletion.
    Lock timeout aborts that transaction; the caller must never catch it and
    proceed with purge. This primitive itself deletes nothing.
    """
    identifiers = tuple(campaigns)
    if not identifiers or any(not isinstance(value, UUID) for value in identifiers):
        raise TypeError("Drain requires canonical campaign identifiers.")
    db = connections["default"]
    if db.vendor != "postgresql" or not db.in_atomic_block:
        raise StorageInvariantError("Campaign drainage requires an owning transaction.")
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('lock_timeout', %s, true)",
            [str(limits.drain_seconds * 1000)],
        )
        for identifier in sorted(set(identifiers)):
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)",
                [READ_NAMESPACE, campaign_lock_key(identifier)],
            )
