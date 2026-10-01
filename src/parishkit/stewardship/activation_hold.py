"""Wait out the brief interval while a configuration change activates (#429).

Applying a configuration request selects the new YAML manifest, then commits
the database pointer in a separate transaction. For about a second between the
two, every authority check raises AuthorityChanging. Work that meets it retries
the same short, re-runnable step a few times instead of failing.

A step is retried only when no transaction is open: activation needs the
deployment-wide work-order lock, so waiting while holding it (or any other
lock) would only delay the activation being waited for.

The installer holds its session lock from the YAML selection through the
database activation. A mismatch seen twice in a row while that lock is free
is not an activation in progress but one that failed after selecting the YAML;
it is raised as the ordinary ConfigError that requires recovery, at once.

A wait that outlasts its limit hands the AuthorityChanging back as a hold.
Background work records that timeout durably (task_timed_out at WARNING, with
what, limit and elapsed: the timeout-logging rule). A web request logs it to
the process log only: every open Admin page polls every few seconds, so a
durable entry per poll would flood the timeout log during a long activation.
"""

import logging
import time

from django.db import DatabaseError, connection

from parishkit.config import ConfigError

from .accounts.authority import AuthorityChanging
from .accounts.installation_lock import INSTALLER_LOCK
from .observability import Event, emit

# How long background work waits for one activation before handing it back.
# An activation commits in about a second; this allows for a slow database.
WORKER_HOLD_SECONDS = 15
# A web request holds a server worker while it waits, so it waits less.
WEB_HOLD_SECONDS = 3
POLL_SECONDS = 0.25
# The timeout entry's ``what`` (audit.schemas.TIMEOUT_KINDS).
TIMEOUT_NAME = "configuration_activation"


def installation_running():
    """Whether any session holds the configuration installer's lock now."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' "
            "AND classid=%s AND objid=%s AND objsubid=2 AND granted)",
            INSTALLER_LOCK,
        )
        return cursor.fetchone()[0]


def activating(error):
    """Whether ``error`` is an activation the installer is still running.

    For callers that do not wait (the scheduler's passes run again within
    seconds). A failed lock read counts as not activating, so the caller
    keeps its ordinary error handling.
    """
    if not isinstance(error, AuthorityChanging):
        return False
    try:
        return installation_running()
    except DatabaseError:
        return False


def _gave_up(task_id, limit, elapsed, durable):
    """Log a wait that reached its limit: durably for background work."""
    if durable:
        from .audit.timeouts import record_timeout

        record_timeout(
            Event.TASK_TIMED_OUT,
            what=TIMEOUT_NAME,
            level="WARNING",
            task_id=task_id,
            limit_seconds=limit,
            elapsed_seconds=elapsed,
        )
    else:
        emit(
            Event.TASK_TIMED_OUT,
            level=logging.WARNING,
            task_id=task_id,
            timeout=TIMEOUT_NAME,
            limit_seconds=round(limit),
            elapsed_seconds=round(elapsed),
        )


def wait_out_activation(step, *, limit=None, task_id=None, durable=True):
    """Return ``step()``, retrying it while a configuration change activates.

    ``step`` must be safe to repeat: it either finishes or raises before any
    effect commits. Inside a transaction the first AuthorityChanging is raised
    at once, for the caller's own hold handling. ``limit`` defaults to
    WORKER_HOLD_SECONDS, read at call time; ``durable`` says whether giving up
    writes a timeout entry (see the module docstring).
    """
    if limit is None:
        limit = WORKER_HOLD_SECONDS
    started = time.monotonic()
    idle = False
    while True:
        try:
            return step()
        except AuthorityChanging as error:
            if connection.in_atomic_block:
                raise
            # One idle sighting may be the instant after the activation
            # committed and released the lock; the step is tried once more.
            if installation_running():
                idle = False
            elif idle:
                raise ConfigError(
                    "Configuration requires recovery; no change is activating."
                ) from error
            else:
                idle = True
            elapsed = time.monotonic() - started
            if elapsed >= limit:
                _gave_up(task_id, limit, elapsed, durable)
                raise
            time.sleep(POLL_SECONDS)
