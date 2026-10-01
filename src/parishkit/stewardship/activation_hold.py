"""Wait out the brief interval while a configuration change activates (#429).

Applying a configuration request selects the new YAML manifest, then commits
the database pointer in a separate transaction. For about a second between the
two, every authority check raises AuthorityChanging. Work that meets it retries
the same short, re-runnable step a few times instead of failing.

A step is retried only when no transaction is open: activation needs the
deployment-wide work-order lock, so waiting while holding it (or any other
lock) would only delay the activation being waited for. A wait that outlasts
its limit means the change did not finish (the installer recovers it); the
caller's AuthorityChanging propagates as a hold, and the limit, the elapsed
time and the task are logged at WARNING under the timeout-logging rule.
"""

import logging
import time

from django.db import connection

from .accounts.authority import AuthorityChanging
from .observability import Event, emit

# How long background work waits for one activation before handing it back.
# An activation commits in about a second; this allows for a slow database.
WORKER_HOLD_SECONDS = 15
# A web request holds a server worker while it waits, so it waits less.
WEB_HOLD_SECONDS = 3
POLL_SECONDS = 0.25
# The process-log name of this limit (observability.TIMEOUT_LIMITS).
TIMEOUT_NAME = "configuration_activation"


def wait_out_activation(step, *, limit=None, task_id=None):
    """Return ``step()``, retrying it while a configuration change activates.

    ``step`` must be safe to repeat: it either finishes or raises before any
    effect commits. Inside a transaction the first AuthorityChanging is raised
    at once, for the caller's own hold handling. ``limit`` defaults to
    WORKER_HOLD_SECONDS, read at call time.
    """
    if limit is None:
        limit = WORKER_HOLD_SECONDS
    started = time.monotonic()
    while True:
        try:
            return step()
        except AuthorityChanging:
            if connection.in_atomic_block:
                raise
            elapsed = time.monotonic() - started
            if elapsed >= limit:
                emit(
                    Event.TASK_TIMED_OUT,
                    level=logging.WARNING,
                    task_id=task_id,
                    timeout=TIMEOUT_NAME,
                    limit_seconds=int(limit),
                    elapsed_seconds=int(elapsed),
                )
                raise
            time.sleep(POLL_SECONDS)
