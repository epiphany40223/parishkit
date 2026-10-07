"""Keep a mail consumer's database connection between messages (#365).

Stewardship processes open a database connection for each unit of work and
close it afterwards (``CONN_MAX_AGE`` is 0). For the mail-dispatch consumers
that cost about four new connections per Family message, each with its
connect-time admission (``accounts.credential_database``) and a cold
SQL-function plan cache, so the dispatch guards were planned again for every
message (``family_mail_dispatch.plan_submission_guards``). The local launch
rehearsal of 2026-09-30 measured a warm connection at 8% less send time and
20% less work-order lock hold per message.

A mail-dispatch process calls ``keep_connections()`` once at startup. After
that, ``release()`` keeps its main thread's connection when it is clean and
closes everything else, exactly as ``connections.close_all()`` did. Every
other process never calls ``keep_connections()``, so ``release()`` closes
everything there.

Why keeping it is safe:

- **Connection budget (#336).** ``database_provisioning.role_limit`` gives each
  mail process three connections: task, lease renewal and timeout log. The
  kept connection is the task connection, on the main thread, which also runs
  the in-flight ownership check during SMTP. The renewal thread and the timeout
  log keep their own short connections, so peak use does not grow.
- **Session state.** The mail path sets only transaction-local settings and
  takes only transaction-scoped advisory locks, so nothing carries over to the
  next message. A connection inside a transaction or an atomic block, or one
  Django saw an error on, is closed.
- **Admission.** A connection is kept for at most ``MAX_AGE_SECONDS``, so it
  passes the connect-time role check again at least that often. Per-task
  ownership and admission checks run in SQL as before.
- **A server-ended connection.** ``refresh()``, at the start of each hint,
  closes a kept connection that no longer answers, so the message reconnects
  instead of failing.
"""

from threading import current_thread, main_thread
from time import monotonic
from weakref import WeakKeyDictionary

from django.db import connections
from psycopg.pq import TransactionStatus

# How long one database connection is kept, from the first time release()
# keeps it. Bounds how stale its connect-time admission can be.
MAX_AGE_SECONDS = 300

_enabled = False
# Each kept Django connection wrapper -> (its driver connection, when first kept).
# A new driver connection behind the same wrapper restarts the clock.
_kept = WeakKeyDictionary()


def keep_connections():
    """Let this process keep its main thread's clean connection between messages."""
    global _enabled
    _enabled = True


def release():
    """Close this thread's database connections, except one that may be reused.

    Called where the mail path used to call ``connections.close_all()``. A
    connection is kept only in a process that called ``keep_connections()``,
    on the main thread, and only while ``_reusable`` says it is clean.
    """
    for database in connections.all(initialized_only=True):
        if not _reusable(database):
            _kept.pop(database, None)
            database.close()


def refresh():
    """Before a hint runs, close a kept connection that is too old or has ended.

    A consumer can sit idle for hours between messages, so the age is checked
    here as well as in ``release()``. A connection the server ended (a
    restart, or an operator's ``pg_terminate_backend``) fails ``drop_unusable``'s
    ``SELECT 1`` and is closed, so the hint opens a new, fully admitted one.
    """
    if not _enabled:
        return
    from parishkit.stewardship.runtime_process import drop_unusable

    for database in connections.all(initialized_only=True):
        since = _kept.get(database)
        if since is not None and monotonic() - since[1] >= MAX_AGE_SECONDS:
            _kept.pop(database, None)
            database.close()
    drop_unusable(connections)


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
    since = _kept.get(database)
    if since is None or since[0] is not raw:
        _kept[database] = (raw, monotonic())
        return True
    return monotonic() - since[1] < MAX_AGE_SECONDS
