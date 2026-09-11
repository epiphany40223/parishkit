"""Whole single-container consumer evidence from admitted live Gunicorn workers.

Workers report what they loaded, not what an installer has subsequently renamed.
The operator must recreate the consumer container: its individual read-only file
mounts pin the old inodes through ordinary worker reloads. This module does not
control Docker or claim that one replica can acknowledge other replicas.
"""

import json
import os
import re
from pathlib import Path

from parishkit.config import ConfigError

from .accounts.key_files import _unique_object, read_private, write_private
from .deployment import ServiceRole
from .runtime_paths import private_directory

DIRECTORY = Path("/tmp/stewardship-consumer")
PIDFILE = DIRECTORY / "supervisor.pid"


def publish_supervisor_identity(server):
    """Record the admitted master in private tmpfs before it forks any workers."""
    private_directory(DIRECTORY)
    write_private(PIDFILE, str(os.getpid()).encode("ascii"))


def process_identity(pid):
    """Bind a Linux PID to its start time so PID reuse cannot replay readiness."""
    if type(pid) is not int or not 1 < pid < 2**31:
        raise ConfigError("Consumer process identity is invalid.")
    try:
        with Path(f"/proc/{pid}/stat").open(encoding="ascii") as stream:
            value = stream.read(4097)
        if len(value) > 4096:
            raise ValueError
        prefix, fields = value.rsplit(") ", 1)
        if int(prefix.split(" ", 1)[0]) != pid:
            raise ValueError
        fields = fields.split()
        if fields[0] in {"Z", "X", "x"}:
            raise ValueError
        return int(fields[1]), int(fields[19])
    except (OSError, ValueError, IndexError, UnicodeError):
        raise ConfigError("Consumer process is unavailable.") from None


def _receipts(values, names):
    """The worker report contains only the exact target inventory and safe labels."""
    if (
        type(values) is not dict
        or set(values) != set(names)
        or any(
            type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in values.values()
        )
    ):
        raise ConfigError("Consumer receipt inventory is invalid.")
    return values


def publish_worker_receipts(worker):
    """Gunicorn calls this only after a worker's application admission succeeds."""
    from django.conf import settings

    configuration = settings.STEWARDSHIP_HEALTH_RUNTIME.configuration
    receipts = _receipts(
        settings.STEWARDSHIP_LOADED_CREDENTIAL_RECEIPTS, configuration.secrets
    )
    private_directory(DIRECTORY)
    pid = os.getpid()
    parent, started = process_identity(pid)
    _, parent_started = process_identity(parent)
    if worker.pid != pid:
        raise ConfigError("Gunicorn worker identity differs from its process.")
    write_private(
        DIRECTORY / f"{pid}-{started}.json",
        json.dumps(
            {
                "version": 1,
                "pid": pid,
                "started": started,
                "parent": parent,
                "parent_started": parent_started,
                "receipts": receipts,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii"),
    )


def _children(supervisor):
    """Read only the supervised process set, never a broad host process inventory."""
    path = Path(f"/proc/{supervisor}/task/{supervisor}/children")
    try:
        with path.open(encoding="ascii") as stream:
            value = stream.read(4097)
        if len(value) > 4096:
            raise ValueError
        children = tuple(sorted(int(item) for item in value.split()))
        if len(set(children)) != len(children):
            raise ValueError
        return children
    except (OSError, ValueError, UnicodeError):
        raise ConfigError("Consumer supervisor evidence is unavailable.") from None


def loaded_service_receipts(configuration):
    """Refuse partial startup, stale PIDs, disagreement or unsupported replicas."""
    if (
        configuration.service_role is not ServiceRole.WEB
        or configuration.runtime_budget.replicas != 1
    ):
        raise ConfigError(
            "Consumer acknowledgement requires one supervised web container."
        )
    private_directory(DIRECTORY)
    try:
        supervisor = int(read_private(PIDFILE).strip())
        _, supervisor_started = process_identity(supervisor)
        children = _children(supervisor)
        if len(children) != configuration.runtime_budget.web_processes:
            raise ValueError
        agreed = None
        for child in children:
            parent, started = process_identity(child)
            if parent != supervisor:
                raise ValueError
            value = json.loads(
                read_private(DIRECTORY / f"{child}-{started}.json"),
                object_pairs_hook=_unique_object,
            )
            if type(value) is not dict or set(value) != {
                "version",
                "pid",
                "started",
                "parent",
                "parent_started",
                "receipts",
            }:
                raise ValueError
            expected = {
                "version": 1,
                "pid": child,
                "started": started,
                "parent": supervisor,
                "parent_started": supervisor_started,
            }
            if any(
                type(value[key]) is not int or value[key] != item
                for key, item in expected.items()
            ):
                raise ValueError
            receipts = _receipts(value["receipts"], configuration.secrets)
            if agreed is not None and agreed != receipts:
                raise ValueError
            agreed = receipts
        if (
            agreed is None
            or _children(supervisor) != children
            or process_identity(supervisor)[1] != supervisor_started
        ):
            raise ValueError
        return agreed
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ConfigError(
            "Not every live consumer worker has matching loaded credentials."
        ) from None
