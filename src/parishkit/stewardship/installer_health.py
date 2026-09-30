"""Private process-local liveness evidence, not queue or provider readiness."""

import json
import os
from time import monotonic

from .accounts.key_files import read_private, write_private
from .consumer_runtime import process_identity

# The container health probe reads this heartbeat without importing the app.
from .probe import DIRECTORY, HEARTBEAT, MAX_AGE_SECONDS
from .runtime_paths import private_directory

# The worker container's source consumer (#336) publishes its own liveness
# here; the worker process checks it and stops when it goes stale.
SOURCE_HEARTBEAT = DIRECTORY / "source-heartbeat.json"
# Likewise the mail-dispatch container's second mail consumer.
MAIL_HEARTBEAT = DIRECTORY / "mail-heartbeat.json"
# Present once either mail consumer has stopped Family mail after a SYSTEMIC
# fault; both then stop until the container's main process starts again.
MAIL_SYSTEMIC_STOP = DIRECTORY / "mail-systemic-stop"


def mark_stopped(path):
    """Create a private stop marker for the other processes in this container."""
    private_directory(path.parent, create=True)
    write_private(path, b"stopped")


def clear_stopped(path):
    """Remove a stop marker left by this container's previous run."""
    path.unlink(missing_ok=True)


def publish_heartbeat(path=None):
    """Only completion of a loop pass refreshes evidence; hangs become unhealthy.

    ``path`` selects a sibling process's own file; the default is the one the
    container health probe reads.
    """
    private_directory(DIRECTORY, create=True)
    pid = os.getpid()
    _, started = process_identity(pid)
    write_private(
        path or HEARTBEAT,
        json.dumps({"pid": pid, "started": started, "time": monotonic()}).encode(
            "ascii"
        ),
    )


def heartbeat_age(path=None):
    """Seconds since a live process last published ``path``, or None.

    None means no usable evidence: a missing or malformed file, or one written
    by a process that is no longer running.
    """
    try:
        private_directory(DIRECTORY)
        value = json.loads(read_private(path or HEARTBEAT, maximum=1024))
        if type(value) is not dict or set(value) != {"pid", "started", "time"}:
            return None
        if type(value["started"]) is not int or type(value["time"]) not in {int, float}:
            return None
        if process_identity(value["pid"])[1] != value["started"]:
            return None
        age = monotonic() - value["time"]
        return age if age >= 0 else None
    except Exception:
        return None


def healthcheck(path=None):
    """A bounded local read verifies live PID/start identity and recent progress."""
    age = heartbeat_age(path)
    return 0 if age is not None and age <= MAX_AGE_SECONDS else 1
