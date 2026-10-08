"""Keep a mail consumer's database connection between messages (#365).

Stewardship processes open a database connection for each unit of work and
close it afterwards (``CONN_MAX_AGE`` is 0). For the mail-dispatch consumers
that cost about two new connections per Family message, each with its
PostgreSQL login checks (``accounts.credential_database``) and a cold
SQL-function plan cache, so the dispatch guards were planned again for every
message (``family_mail_dispatch.plan_submission_guards``). Keeping the
connection made a one-at-a-time send about 10% faster in the #365
measurement.

A mail-dispatch process calls ``keep_connections()`` once at startup. After
that, ``release()`` keeps its main thread's connection when it is clean and
closes everything else, exactly as ``connections.close_all()`` did. Every
other process never calls ``keep_connections()``, so ``release()`` closes
everything there.

The kept session carries over from one message to the next. Only what that
is safe for may carry over: SQL-function plans (the point of keeping it;
see ``plan_submission_guards``), and nothing else the mail path sets.

Why keeping it is safe:

- **Connection budget (#336).** ``database_provisioning.role_limit`` gives each
  mail process three connections: task, lease renewal and timeout log. The
  kept connection is the task connection, on the main thread, which also runs
  the in-flight ownership check during SMTP. The renewal thread and the timeout
  log keep their own short connections, so peak use does not grow. At rest
  each mail consumer holds one idle session, two for the container.
- **Session state.** The mail path sets only transaction-local settings and
  takes only transaction-scoped advisory locks. As a backstop, ``release()``
  runs ``pg_advisory_unlock_all()`` on a kept session, so no session-level
  advisory lock can outlive its message. A connection inside a transaction
  or an atomic block, or one Django saw an error on, is closed instead.
  (``DISCARD ALL`` or ``RESET ALL`` would also throw away the cached plans.)
- **Login checks.** A connection is not reused once ``MAX_AGE_SECONDS`` old,
  so the PostgreSQL login checks run again at least that often between
  messages (an idle consumer's session may stay open until its next message,
  which then reconnects). Per-task ownership and admission checks run in SQL
  as before.
- **A server-ended connection.** ``refresh()``, at the start of each hint, and
  ``drop_unusable()`` after SMTP and before the outcome is recorded, close a
  kept connection that no longer answers (#639), so a PostgreSQL restart
  reconnects instead of failing the message. TCP keepalives
  (``KEEPALIVES``) let the operating system notice a dead peer on an idle
  kept session.
"""

import logging
from dataclasses import dataclass
from threading import current_thread, main_thread
from time import monotonic
from weakref import WeakKeyDictionary

from django.db import connections
from psycopg.pq import TransactionStatus

from parishkit.logging import log_extra

LOG = logging.getLogger(__name__)
# How long one database connection may be reused, from the first time
# release() keeps it. Bounds how long ago its login checks ran.
MAX_AGE_SECONDS = 300
# libpq TCP keepalive options for a process that keeps its connection: probe
# an idle session after 60 s, every 15 s, and give up after 4 missed probes.
KEEPALIVES = {
    "keepalives": 1,
    "keepalives_idle": 60,
    "keepalives_interval": 15,
    "keepalives_count": 4,
}


@dataclass(frozen=True)
class _Kept:
    """The driver connection a wrapper kept, and when it was first kept."""

    raw: object
    since: float


_enabled = False
# Each kept Django connection wrapper -> its _Kept. A new driver connection
# behind the same wrapper restarts the clock.
_kept = WeakKeyDictionary()
# Whether the current hint started on a kept session (the "db_kept" send
# statistic); set by refresh().
_hint_kept = False


def keep_connections():
    """Let this process keep its main thread's clean connection between messages.

    Also turns on TCP keepalives for this process's database connections, and
    logs once that the process keeps its connection.
    """
    global _enabled
    from django.conf import settings

    for database in settings.DATABASES.values():
        database.setdefault("OPTIONS", {}).update(KEEPALIVES)
    _enabled = True
    LOG.info(
        "Mail consumer keeps its database connection between messages.",
        extra=log_extra({"connection_reuse": True, "max_age_seconds": MAX_AGE_SECONDS}),
    )


def release():
    """Close this thread's database connections, except one that may be reused.

    Called where the mail path used to call ``connections.close_all()``. A
    connection is kept only in a process that called ``keep_connections()``,
    on the main thread, and only while ``_reusable`` says it is clean. A kept
    session's advisory locks are released; if that fails, it is closed.
    """
    for database in connections.all(initialized_only=True):
        if _reusable(database) and _unlock(database):
            continue
        _kept.pop(database, None)
        database.close()


def refresh():
    """Before a hint runs, close a kept connection that is too old or has ended.

    A consumer can sit idle for hours between messages, so the age is checked
    here as well as in ``release()``. A connection the server ended (a
    restart, or an operator's ``pg_terminate_backend``) fails ``drop_unusable``'s
    ``SELECT 1`` and is closed, so the hint opens a new, fully admitted one.
    Records whether the hint starts on a kept session (``hint_kept``).
    """
    global _hint_kept
    _hint_kept = False
    if not _enabled:
        return
    for database in connections.all(initialized_only=True):
        if _expired(_kept.get(database)):
            _kept.pop(database, None)
            database.close()
    drop_unusable()
    _hint_kept = connections["default"].connection is not None


def hint_kept():
    """Whether the current hint started on a kept database session."""
    return _hint_kept


def drop_unusable():
    """Close each of this thread's connections that no longer answers (#639)."""
    from parishkit.stewardship.runtime_process import drop_unusable as drop

    drop(connections)


def _expired(kept):
    """Whether a kept connection has reached its maximum age."""
    return kept is not None and monotonic() - kept.since >= MAX_AGE_SECONDS


def _unlock(database):
    """Release any session-level advisory lock; False if that failed."""
    try:
        with database.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock_all()")
        return True
    except Exception:
        return False


def _reusable(database):
    """Whether ``database`` is open, idle, error-free and young enough to keep.

    Anything unexpected (a closed driver connection, a failed status read)
    counts as not reusable, so the caller closes it as before.
    """
    raw = database.connection
    if not _enabled or raw is None or current_thread() is not main_thread():
        return False
    try:
        clean = (
            not database.in_atomic_block
            and not database.needs_rollback
            and not database.errors_occurred
            and database.get_autocommit()
            and raw.info.transaction_status == TransactionStatus.IDLE
        )
    except Exception:
        return False
    if not clean:
        return False
    kept = _kept.get(database)
    if kept is None or kept.raw is not raw:
        _kept[database] = _Kept(raw, monotonic())
        return True
    return not _expired(kept)
