"""The web container's Gunicorn master, which durably logs the workers it kills.

Gunicorn's master (the arbiter) kills a web worker in two cases, both at a time
limit (#374):

- ``web_drain``: on a stop it asks every worker to finish its in-flight
  requests and exit, waits ``graceful_timeout``, then SIGKILLs any worker still
  serving. The limit is that grace; the elapsed time is measured from when the
  stop began.
- ``web_heartbeat``: a worker that stops reporting to the master for longer
  than ``timeout`` is aborted (SIGABRT, then SIGKILL if it lingers). The limit
  is that timeout; the elapsed time is how long the worker has been silent.

A SIGKILL cannot be caught inside the worker, and a worker that is still
serving may never reach its own exit hooks, but the master survives the kill.
So the master records the entry itself, just before it kills: an ERROR
``helper_timed_out`` entry in ``stewardship_operational_log``, in the
reviewed ``timeout`` context, with the limit, the elapsed seconds and, for a
drain, how many workers were killed. That is the timeout-logging rule's
durable record (audit.timeouts, which a Django process uses).

The master deliberately never loads Django, so this module writes the entry
with psycopg directly, on the web's own SQL login (read from its password file
only when needed), with short connect, statement and lock limits. It never
raises, and the master waits at most RECORD_SECONDS for it: the process log
gets the same facts first, so a refused or slow write cannot block the stop or
lose them. ``graceful_timeout`` is the container's stop grace less
runtime_budget.STOP_MARGIN_SECONDS, so the drain kill and its entry come
before Docker's own kill of the container.
"""

import json
import logging
import signal
from contextlib import suppress
from threading import Thread
from time import monotonic
from uuid import uuid4

from gunicorn.arbiter import Arbiter

from .audit.schemas import ContextKind, sanitize
from .observability import Event, FailureKind, emit

# The longest the master waits for one durable entry before it kills anyway.
# The write's own connect, statement and lock limits are shorter.
RECORD_SECONDS = 5
# Gunicorn's quick shutdown: a SIGINT or SIGQUIT during a graceful stop cuts
# what is left of it to this many seconds (Arbiter.stop).
QUICK_SHUTDOWN_SECONDS = 2

INSERT = (
    "INSERT INTO stewardship_operational_log "
    "(id,correlation_id,event,level,schema,context) "
    "VALUES (%s,%s,%s,%s,%s,%s::jsonb)"
)


def _seconds(value):
    """Whole, non-negative seconds for the log."""
    return max(0, int(round(value)))


def _insert(settings, context):
    """Insert one timeout entry on a fresh, short-lived connection.

    ``settings`` is a Django-style database dictionary (runtime_database's
    ``database_settings``). The connection commits at once and is closed.
    """
    import psycopg

    with psycopg.connect(
        host=settings["HOST"],
        port=settings["PORT"],
        dbname=settings["NAME"],
        user=settings["USER"],
        password=settings["PASSWORD"],
        sslmode=settings.get("OPTIONS", {}).get("sslmode", "disable"),
        connect_timeout=2,
        tcp_user_timeout=2000,
        options="-c statement_timeout=2s -c lock_timeout=1s",
        autocommit=True,
    ) as db:
        db.execute(
            INSERT,
            [
                uuid4(),
                uuid4(),
                Event.HELPER_TIMED_OUT.value,
                "ERROR",
                ContextKind.TIMEOUT.value,
                json.dumps(context),
            ],
        )


def record_web_kill(database, *, what, limit_seconds, elapsed_seconds, count=None):
    """Log a killed web worker to the process log, then durably; never raise.

    ``database`` is a callable returning the database settings, so a missing
    or unreadable password file is just another failed write.
    """
    try:
        limit, elapsed = _seconds(limit_seconds), _seconds(elapsed_seconds)
        # The process log first, so the facts survive a failed durable write.
        emit(
            Event.HELPER_TIMED_OUT,
            level=logging.ERROR,
            timeout=what,
            limit_seconds=limit,
            elapsed_seconds=elapsed,
        )
        values = {"what": what, "limit_seconds": limit, "elapsed_seconds": elapsed}
        if count is not None:
            values["count"] = count
        _insert(database(), sanitize(ContextKind.TIMEOUT, values))
    except Exception as error:
        # Never let recording stop the stop. psycopg is imported only by
        # _insert, so its errors are told apart by their module.
        database_error = type(error).__module__.startswith("psycopg")
        with suppress(Exception):
            emit(
                Event.HELPER_TIMED_OUT,
                level=logging.ERROR,
                failure_kind=FailureKind.DATABASE
                if database_error
                else FailureKind.UNEXPECTED,
            )


def record_within(seconds, database, **values):
    """Record like ``record_web_kill``, but wait at most ``seconds`` for it.

    The write runs on a daemon thread, so a database that hangs past the
    write's own limits cannot hold the master, and the kill that follows,
    for longer than ``seconds``. Never raises: a thread that cannot start
    (the host is out of memory or processes) must not crash the master's
    main loop or skip the kill that follows, so it is only logged.
    """
    try:
        writer = Thread(
            target=record_web_kill,
            args=(database,),
            kwargs=values,
            name="stewardship-web-kill-log",
            daemon=True,
        )
        writer.start()
        writer.join(timeout=seconds)
    except Exception:
        with suppress(Exception):
            emit(
                Event.HELPER_TIMED_OUT,
                level=logging.ERROR,
                failure_kind=FailureKind.UNEXPECTED,
            )


class RecordingArbiter(Arbiter):
    """Gunicorn's master, logging each worker it kills at a limit (see module)."""

    def __init__(self, app, *, database):
        """``database`` returns the web's SQL settings when an entry is written."""
        self.stewardship_database = database
        self.stewardship_stop_began = None
        super().__init__(app)

    def stop(self, graceful=True):
        """Note when the first stop began; its drain kill is measured from it."""
        if self.stewardship_stop_began is None:
            self.stewardship_stop_began = monotonic()
        super().stop(graceful)

    def kill_workers(self, sig):
        """Record the workers a stop is about to SIGKILL at its limit, then kill.

        Only ``stop`` sends SIGKILL to every worker, once its drain has run
        out. ``stop`` reaps only every 0.1 s, so workers that exited since are
        reaped first: only workers still running are counted and logged.

        A stop that ends before ``graceful_timeout`` was cut short by
        Gunicorn's quick shutdown (SIGINT or SIGQUIT), whose limit is
        QUICK_SHUTDOWN_SECONDS; the entry names that limit instead.
        """
        if sig == signal.SIGKILL and self.stewardship_stop_began is not None:
            self.reap_workers()
            if self.WORKERS:
                elapsed = monotonic() - self.stewardship_stop_began
                limit = self.cfg.graceful_timeout
                if elapsed < limit:
                    limit = QUICK_SHUTDOWN_SECONDS
                record_within(
                    RECORD_SECONDS,
                    self.stewardship_database,
                    what="web_drain",
                    limit_seconds=limit,
                    elapsed_seconds=elapsed,
                    count=len(self.WORKERS),
                )
        super().kill_workers(sig)

    def kill_worker(self, pid, sig):
        """Record a worker aborted for silence past ``timeout``, then abort it.

        Gunicorn aborts a silent worker with SIGABRT and SIGKILLs it only if
        it is still silent afterwards; the one entry is written at the abort.
        """
        worker = self.WORKERS.get(pid)
        if sig == signal.SIGABRT and worker is not None:
            try:
                silent = monotonic() - worker.tmp.last_update()
            except (OSError, ValueError):
                silent = self.timeout
            record_within(
                RECORD_SECONDS,
                self.stewardship_database,
                what="web_heartbeat",
                limit_seconds=self.timeout,
                elapsed_seconds=silent,
            )
        super().kill_worker(pid, sig)
