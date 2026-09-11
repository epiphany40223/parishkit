"""Private process-local liveness evidence, not queue or provider readiness."""

import json
import os
from pathlib import Path
from time import monotonic

from .accounts.key_files import read_private, write_private
from .consumer_runtime import process_identity
from .runtime_paths import private_directory

DIRECTORY = Path("/tmp/stewardship-installer")
HEARTBEAT = DIRECTORY / "heartbeat.json"
MAX_AGE_SECONDS = 90


def publish_heartbeat():
    """Only completion of a loop pass refreshes evidence; hangs become unhealthy."""
    private_directory(DIRECTORY, create=True)
    pid = os.getpid()
    _, started = process_identity(pid)
    write_private(
        HEARTBEAT,
        json.dumps({"pid": pid, "started": started, "time": monotonic()}).encode(
            "ascii"
        ),
    )


def healthcheck():
    """A bounded local read verifies live PID/start identity and recent progress."""
    try:
        private_directory(DIRECTORY)
        value = json.loads(read_private(HEARTBEAT, maximum=1024))
        if type(value) is not dict or set(value) != {"pid", "started", "time"}:
            return 1
        if type(value["started"]) is not int or type(value["time"]) not in {int, float}:
            return 1
        return (
            0
            if (
                process_identity(value["pid"])[1] == value["started"]
                and 0 <= monotonic() - value["time"] <= MAX_AGE_SECONDS
            )
            else 1
        )
    except Exception:
        return 1
