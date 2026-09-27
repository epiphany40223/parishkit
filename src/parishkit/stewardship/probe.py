"""Standard-library-only container health probes.

Docker runs a health check as a new process in every online service, once a
minute. Importing the application for that (Django, cryptography, the CLI)
costs about a CPU-second per check, and eighteen services checking in the same
second spiked a 4-vCPU host's load past 15. These probes import nothing but
the standard library, so a check costs a few milliseconds.

`python -m parishkit.stewardship.probe installer` checks the heartbeat that a
consumer loop publishes (see installer_health.publish_heartbeat); `... web`
checks the web server's local liveness endpoint. Each exits 0 when healthy.
"""

import json
import os
import stat
import sys
from pathlib import Path
from time import monotonic

DIRECTORY = Path("/tmp/stewardship-installer")
HEARTBEAT = DIRECTORY / "heartbeat.json"
MAX_AGE_SECONDS = 90
MAX_HEARTBEAT_BYTES = 1024
LIVENESS_URL = "http://127.0.0.1:8000/health/live"


def _private_directory(path):
    """The heartbeat directory must be this user's own, mode 0700, not a link."""
    metadata = os.lstat(path)
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and stat.S_IMODE(metadata.st_mode) == 0o700
    )


def _read_private(path, maximum):
    """Read one owner-only regular file without following a final symlink."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= maximum
        ):
            return None
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            return stream.read(maximum + 1)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def process_started(pid):
    """A live PID's start time in clock ticks, or None; PID reuse cannot match."""
    if type(pid) is not int or not 1 < pid < 2**31:
        return None
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stream:
            value = stream.read(4097)
        prefix, fields = value.rsplit(") ", 1)
        fields = fields.split()
        if len(value) > 4096 or int(prefix.split(" ", 1)[0]) != pid:
            return None
        return None if fields[0] in {"Z", "X", "x"} else int(fields[19])
    except (OSError, ValueError, IndexError, UnicodeError):
        return None


def installer():
    """0 when the publishing process is alive and passed its loop recently."""
    try:
        if not _private_directory(DIRECTORY):
            return 1
        raw = _read_private(HEARTBEAT, MAX_HEARTBEAT_BYTES)
        if raw is None or len(raw) > MAX_HEARTBEAT_BYTES:
            return 1
        value = json.loads(raw)
        if type(value) is not dict or set(value) != {"pid", "started", "time"}:
            return 1
        if type(value["started"]) is not int or type(value["time"]) not in {
            int,
            float,
        }:
            return 1
        alive = process_started(value["pid"]) == value["started"]
        return 0 if alive and 0 <= monotonic() - value["time"] <= MAX_AGE_SECONDS else 1
    except (OSError, ValueError, UnicodeError, RecursionError):
        return 1


def web():
    """0 when the local web server answers its liveness endpoint with "ok"."""
    from urllib.error import URLError
    from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

    class NoRedirect(HTTPRedirectHandler):
        """A redirected response fails rather than probing another endpoint."""

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(LIVENESS_URL, timeout=3) as response:
            return 0 if response.status == 200 and response.read(4) == b"ok\n" else 1
    except (URLError, OSError, ValueError):
        return 1


def main(argv=None):
    """Run the named probe; an unknown name is unhealthy, never a traceback."""
    arguments = sys.argv[1:] if argv is None else argv
    probes = {"installer": installer, "web": web}
    if len(arguments) != 1 or arguments[0] not in probes:
        return 2
    return probes[arguments[0]]()


if __name__ == "__main__":
    sys.exit(main())
