"""The LOCAL fake clock (#476): one shared, forward-only libfaketime offset.

In the LOCAL profile, and only there, the application containers and the
PostgreSQL and Valkey containers can run under libfaketime, every one of them
reading the same offset file. The seeder builds a campaign's history by moving
that clock forward through the campaign's events while the real scheduler,
worker, mail-dispatch and web code do the work at the faked instants (see
"Fake clock" in docs/specs/stewardship/local-environment/spec.md).

This module holds the constants the Compose renderer, the mount policy and the
seeder share, and the file operations on the offset file libfaketime reads;
the mode marker beside it is the operator script's. Nothing
here is reached by any other profile; the renderer refuses to produce the
override outside LOCAL and the mount policy admits the clock mount only there.
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from parishkit.config import ConfigError

LOGGER = logging.getLogger(__name__)

# Where the derived local images put libfaketime: one fixed path on every
# architecture, so the override does not depend on Debian's multiarch triplet
# (deploy/stewardship/Dockerfile.faketime creates the link).
FAKETIME_LIBRARY = "/usr/local/lib/parishkit-faketime/libfaketime.so.1"
# The clock directory as every faked container sees it (the one mount the
# fake-clock override adds, read-only), and its two files.
CLOCK_MOUNT_TARGET = Path("/run/parishkit-clock")
# Where the seeder's one-shot containers mount the same directory writable,
# so they can move the clock while the services keep their read-only mount.
CLOCK_CONTROL_TARGET = Path("/run/parishkit-clock-control")
OFFSET_FILE = "offset"
# The operator script's mode marker beside the offset: "fake" or "normal".
MODE_FILE = "mode"
# libfaketime re-reads the offset file once this many seconds have passed
# since it last did (FAKETIME_CACHE_DURATION); after writing a new offset the
# seeder waits CACHE_WAIT_SECONDS, longer than the cache, before acting.
CACHE_SECONDS = 1
CACHE_WAIT_SECONDS = 2
# `up` runs the whole install and the setup wizard this far behind real time,
# so a later seed can start its clock forward of every existing row: the
# Friday before any seed's start Saturday is at most 16 days before its now.
UP_OFFSET_SECONDS = -17 * 86400
# The environment the override sets on each faked service. The library path
# and the cache duration are fixed; monotonic clocks stay real so sleeps,
# subprocess timeouts and work budgets behave normally.
FAKETIME_ENVIRONMENT = {
    "LD_PRELOAD": FAKETIME_LIBRARY,
    "FAKETIME_TIMESTAMP_FILE": str(CLOCK_MOUNT_TARGET / OFFSET_FILE),
    "FAKETIME_CACHE_DURATION": str(CACHE_SECONDS),
    "FAKETIME_DONT_FAKE_MONOTONIC": "1",
}
# The faked services are every service that runs the application image plus
# the two stores; Caddy, Mailpit and the fake ParishSoft stay on real time.
FAKETIME_STORES = ("postgres", "valkey")
# The one application-image service that is never faked.
FAKE_PARISHSOFT_SERVICE = "fake-parishsoft"
FAKETIME_IMAGE_PREFIX = "parishkit-stewardship-local-faketime-"


def clock_directory(configuration):
    """The clock directory on the host: ``run/local/clock`` in the runtime root."""
    return configuration.paths["run"] / "local" / "clock"


def faketime_image(base):
    """The derived local image tag for a base image (#476, "Fake clock").

    ``parishkit-stewardship-local-faketime-<base>:<base digest or local tag>``:
    a digest-pinned store image keeps its digest as the tag; the locally built
    application image keeps its local tag. The operator script builds each
    derived image inside the VM ``FROM`` its base with
    deploy/stewardship/Dockerfile.faketime.
    """
    if type(base) is not str or not base:
        raise ConfigError("A base image reference is required.")
    if base.startswith(FAKETIME_IMAGE_PREFIX):
        return base
    if "@sha256:" in base:
        # A pinned store image: "postgres:18.6-trixie@sha256:<hex>" or
        # "valkey/valkey:9.1.2@sha256:<hex>" becomes "…-faketime-postgres:<hex>"
        # or "…-faketime-valkey:<hex>" (a 64-hex tag is within Docker's 128).
        name, digest = base.split("@sha256:", 1)
        repository = name.split(":", 1)[0].rsplit("/", 1)[-1]
        return f"{FAKETIME_IMAGE_PREFIX}{repository}:{digest}"
    name, _, tag = base.rpartition(":")
    if name != "parishkit-stewardship-local" or not tag:
        raise ConfigError("A local build tag or a digest-pinned image is required.")
    return f"{FAKETIME_IMAGE_PREFIX}stewardship:{tag}"


def parse_offset(text):
    """The offset file's content as seconds; libfaketime's relative form only."""
    value = text.strip()
    if not value or value[0] not in "+-" or not value[1:].isdecimal():
        raise ConfigError("The clock offset file is not a signed whole number.")
    return int(value)


def format_offset(seconds):
    """The relative libfaketime specification for an offset in whole seconds."""
    if type(seconds) is not int or isinstance(seconds, bool):
        raise ConfigError("A clock offset must be a whole number of seconds.")
    return f"{seconds:+d}\n"


def read_offset(directory):
    """The current offset in seconds from the clock directory."""
    return parse_offset((Path(directory) / OFFSET_FILE).read_text(encoding="ascii"))


def write_offset(directory, seconds):
    """Replace the offset file atomically, so a reader never sees a partial file."""
    target = Path(directory) / OFFSET_FILE
    staging = target.with_name(OFFSET_FILE + ".new")
    staging.write_text(format_offset(seconds), encoding="ascii")
    staging.replace(target)


def fake_now(offset, *, real_now=None):
    """The fake instant for an offset: real time shifted by the offset."""
    real = datetime.now(UTC) if real_now is None else real_now
    return real + timedelta(seconds=offset)


class FakeClock:
    """The one shared clock, moved forward only.

    Each jump computes the new offset as target minus real now and refuses a
    target earlier than the current fake time, so fake time never runs
    backwards and, because the offset is never positive, never ahead of real
    time. After writing, the clock waits longer than libfaketime's cache
    before returning, so every faked process has read the new offset by the
    time the caller acts. ``now`` and ``sleep`` are injectable for tests.
    """

    def __init__(self, directory, *, now=None, sleep=time.sleep):
        """Operate on a clock directory; ``now`` supplies real UTC instants.

        Without ``now`` the real instant is derived: the seeder itself runs in
        a faked container, where ``datetime.now`` already includes the offset,
        so real time is the faked time minus the offset the file holds (the
        library re-reads the file within CACHE_SECONDS; jumps wait longer).
        In normal mode the offset is zero and the derivation is the identity.
        """
        self.directory = Path(directory)
        self._now = now or self._derived_real_now
        self._sleep = sleep

    def _derived_real_now(self):
        """Real time from a possibly faked process clock and the shared offset."""
        return datetime.now(UTC) - timedelta(seconds=read_offset(self.directory))

    def real_now(self):
        """The real (unfaked) current instant."""
        return self._now()

    def offset(self):
        """The current offset in seconds."""
        return read_offset(self.directory)

    def now(self):
        """The current fake instant."""
        return fake_now(self.offset(), real_now=self._now())

    def jump_to(self, target):
        """Move the clock to ``target`` (never backwards) and wait out the cache.

        Returns the offset written. A target at or before the current fake
        time is not a jump: the clock is left alone and nothing is waited for,
        since fake time has already passed the instant (see "Late instants" in
        the specification).
        """
        if not isinstance(target, datetime) or target.utcoffset() is None:
            raise ConfigError("A clock target must be an aware instant.")
        real = self._now()
        current = fake_now(self.offset(), real_now=real)
        if target <= current:
            return None
        if target > real:
            raise ConfigError("The fake clock cannot run ahead of real time.")
        offset = int((target - real).total_seconds())
        write_offset(self.directory, offset)
        LOGGER.info(
            "fake clock jumped from %s to %s (offset %+d s)",
            current.isoformat(timespec="seconds"),
            target.isoformat(timespec="seconds"),
            offset,
        )
        self._sleep(CACHE_WAIT_SECONDS)
        return offset
