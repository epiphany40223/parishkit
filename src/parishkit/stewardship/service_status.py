"""Each online process's service status record (ADM-13, #530).

Every online application process (each web worker, the worker and its source
process, the scheduler, each mail consumer, the configuration installer and
each credential installer) writes one ``stewardship_service_status`` row when
it starts and refreshes it about every 60 seconds: its version, whether debug
logging is in effect and, for a mail consumer, its Family mail sender's state.
The System health page reads the rows; a row more than three minutes old
shows its process as not running.

The record is for display only. A write that fails is logged once and
skipped; it never stops or slows the process's real work beyond its short
SQL limits, and a write those limits stop is recorded as a timeout (#287).
Writes reuse a database connection the calling thread already holds (or,
where the caller says so, open one it will close), and never run inside
another transaction, so they add no connection to a busy process's budget
and cannot roll back, or be rolled back by, its work.
"""

import logging
from threading import Lock
from time import monotonic
from uuid import uuid4

from parishkit import __version__

from .observability import Event, debug_logging_enabled, emit_failure

# How often a process refreshes its record.
REPORT_SECONDS = 60
# A record older than this shows its process as not running (the page's rule).
NOT_RUNNING_SECONDS = 180
# The write's own SQL limits, in seconds: a stuck write gives up quickly.
STATEMENT_SECONDS = 2
LOCK_SECONDS = 1
# Services whose login has no operational-log grant: their timeouts go to
# the process log only (the operations specification's listed exception).
PROCESS_LOG_ONLY = frozenset({"config-installer", "credential-installer"})

# The guard sets reported_at (and sender_since) itself on every refresh.
_UPDATE = (
    "UPDATE public.stewardship_service_status SET debug_logging=%s, "
    "sender_state=%s, sender_until=statement_timestamp()+make_interval(secs=>%s) "
    "WHERE id=%s"
)
_INSERT = (
    "INSERT INTO public.stewardship_service_status (id,service,process,target,"
    "started_at,reported_at,application_version,debug_logging,sender_state,"
    "sender_until) VALUES (%s,%s,%s,%s,statement_timestamp(),statement_timestamp(),"
    "%s,%s,%s,statement_timestamp()+make_interval(secs=>%s))"
)


class ServiceStatusReporter:
    """Write this process's service status record when it is due.

    ``service`` is the deployment service role's value (``web``, ``worker``,
    ``mail-dispatch`` ...), ``process`` is ``main``, ``source`` or ``mail``
    (see ``jobs.service_status_models``), ``target`` names a credential
    installer's target, and ``sender`` (mail dispatch only) returns the
    Family mail sender's ``(state, seconds_left)``. The record's identity is
    a new UUID per process, so a restart starts a new row.
    """

    def __init__(self, service, *, process="main", target=None, sender=None):
        """Remember what this process is; nothing is written until ``report``."""
        self.service, self.process, self.target = service, process, target
        self.sender = sender
        self.id = uuid4()
        self.due = 0.0
        self.failing = False
        # Heartbeats can arrive on several threads; one writes, others skip.
        self.lock = Lock()

    def report(self, *, connect=False):
        """Write the record if it is due; return whether a write succeeded.

        Without ``connect`` it writes only on a connection this thread
        already has open, so it never adds a connection beside running work;
        with it, the caller allows a new one and closes it afterwards. It
        never writes inside a transaction: it would share that transaction's
        fate and hold its locks longer. Skipped calls stay due, so the next
        suitable call writes.
        """
        if monotonic() < self.due or not self.lock.acquire(blocking=False):
            return False
        try:
            from django.db import connection

            try:
                busy = connection.in_atomic_block or (
                    not connect and connection.connection is None
                )
            except Exception:
                busy = True  # Django is not set up in this process yet.
            if busy:
                return False
            self.due = monotonic() + REPORT_SECONDS
            started = monotonic()
            try:
                self._write(connection)
            except Exception as error:
                self._failed(error, monotonic() - started)
                return False
            self.failing = False
            return True
        finally:
            self.lock.release()

    def _failed(self, error, elapsed):
        """Log a failed write: a SQL time limit as a timeout, else once per run.

        A statement or lock timeout is a write this process's own limit
        stopped (#287). It is recorded through the timeout log as a WARNING
        ``task_timed_out`` with the limit (``statement_timeout`` or
        ``lock_timeout``), its seconds and the time the write took, naming
        no task even when the write ran on a task's thread (a bulk send's
        heartbeat), because no task was stopped. Every one is recorded: a
        process attempts a write at most once every REPORT_SECONDS, so there
        is at most one such entry a minute without any further throttle,
        and each has its process-log line first. The installers have no operational-log
        grant, so theirs go to the process log only (an exception the
        operations specification lists). Any other failure is logged once
        until a write succeeds again.
        """
        from math import ceil

        from .jobs.broker import sql_timeout_kind

        kind = sql_timeout_kind(error)
        if kind not in {"statement_timeout", "lock_timeout"}:
            if not self.failing:
                emit_failure(
                    error, event=Event.SERVICE_STATUS_FAILED, level=logging.WARNING
                )
            self.failing = True
            return
        limit = STATEMENT_SECONDS if kind == "statement_timeout" else LOCK_SECONDS
        elapsed = max(1, ceil(elapsed))
        if self.service in PROCESS_LOG_ONLY:
            from .observability import emit

            emit(
                Event.TASK_TIMED_OUT,
                level=logging.WARNING,
                timeout=kind,
                limit_seconds=limit,
                elapsed_seconds=elapsed,
            )
            return
        from .audit.timeouts import record_timeout

        record_timeout(
            Event.TASK_TIMED_OUT,
            what=kind,
            level="WARNING",
            limit_seconds=limit,
            elapsed_seconds=elapsed,
            bind_task=False,
        )

    def _write(self, connection):
        """Refresh this process's row, inserting it when there is none.

        The update comes first, so a row that a lost commit acknowledgement
        left in place is refreshed rather than inserted twice, and a row the
        worker's housekeeping removed (after a day without reports) is
        inserted again under the same identity. The guard sets every time
        from the database clock; ``sender_until`` is the database clock plus
        the seconds the sender reports.
        """
        from django.db import transaction

        state, seconds = self.sender() if self.sender is not None else (None, None)
        debug = debug_logging_enabled()
        with transaction.atomic(using=connection.alias), connection.cursor() as cursor:
            cursor.execute(f"SET LOCAL statement_timeout='{STATEMENT_SECONDS}s'")
            cursor.execute(f"SET LOCAL lock_timeout='{LOCK_SECONDS}s'")
            cursor.execute(_UPDATE, [debug, state, seconds, self.id])
            if cursor.rowcount == 1:
                return
            cursor.execute(
                _INSERT,
                [
                    self.id,
                    self.service,
                    self.process,
                    self.target,
                    __version__,
                    debug,
                    state,
                    seconds,
                ],
            )


def prune_service_status():
    """Delete the records of processes that have not reported for a day.

    The worker's hourly housekeeping runs this inside its fenced effect; the
    guard refuses any other deletion. Returns how many rows went.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM public.stewardship_service_status "
            "WHERE reported_at<statement_timestamp()-interval '1 day'"
        )
        return cursor.rowcount
