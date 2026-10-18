"""Private process-local liveness evidence, not queue or provider readiness."""

import json
import os
from contextlib import suppress
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


def claim_stop(path, value):
    """Create a private stop marker holding ``value`` unless one exists.

    Returns what the marker holds afterwards: ``value`` when this call
    created it, otherwise the other process's contents (None if they cannot
    be read yet, for example while that process is still writing them).
    The exclusive create means two processes that stop at the same moment
    share the first one's marker (ADM-13 PR 4a: one halt identity).
    """
    private_directory(path.parent, create=True)
    try:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
    except FileExistsError:
        try:
            return read_private(path)
        except Exception:
            return None
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    return value


def read_stop(path):
    """The stop marker's contents, or None when it is absent or unreadable."""
    try:
        return read_private(path)
    except Exception:
        return None


def release_stop(path, owns):
    """Remove the stop marker at ``path`` only if ``owns(contents)`` is true.

    Returns whether it was removed. A read followed by an unlink could
    delete a marker the other process created in between (after its own
    lift and a probe that halted again), so the check and the removal are
    one step: the marker is first renamed aside to a name only this process
    uses, which atomically takes whatever marker is there, and is then
    read. A marker that names something else is linked back, unless a newer
    marker already exists, which then stands; either way the aside name is
    removed. Meanwhile a reader finds no marker, as just after a lift.
    """
    aside = path.with_name(f".{path.name}.lifting-{os.getpid()}")
    try:
        os.rename(path, aside)
    except FileNotFoundError:
        return False
    try:
        value = read_stop(aside)
        if value is not None and owns(value):
            return True
        # Another process's marker (possibly still being written through
        # its open descriptor, which the rename keeps): put it back.
        with suppress(FileExistsError):
            os.link(aside, path)
        return False
    finally:
        aside.unlink(missing_ok=True)


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
