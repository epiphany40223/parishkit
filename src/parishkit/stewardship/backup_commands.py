"""Console entry points for the v1 backup: run it, make its key, open a set.

Every refusal is one fixed sentence; paths, keys and database errors stay in
the process log. `backup` runs in the rendered backup profile beside the
online services; `backup-keygen` and `backup-open` run wherever the operator
keeps the private key, which is never the host.
"""

import json
import os
import sys
from pathlib import Path

from parishkit.config import ConfigError

from .backup_sealing import generate_keypair, load_private, open_sealed
from .deployment import load_deployment
from .observability import Event, configure_logging, emit_failure


def _keygen(destination):
    """Write the private key owner-only to a new file; print the public half."""
    if destination is None:
        raise ConfigError("An explicit private key destination is required.")
    private, public = generate_keypair()
    descriptor = os.open(
        Path(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as stream:
        stream.write(private)
    return {"public_key": public.strip()}


def _open(key, source, destination):
    """Decrypt one sealed file to a new file; refuse anything that is not whole."""
    if key is None or source is None or destination is None:
        raise ConfigError("Key, input and destination are required.")
    private = load_private(key)
    descriptor = os.open(
        Path(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        with Path(source).open("rb") as stream, os.fdopen(descriptor, "wb") as sink:
            kind, count, digest = open_sealed(stream, sink, private=private)
    except BaseException:
        # Partial output is never left where a restore could pick it up.
        Path(destination).unlink(missing_ok=True)
        raise
    return {"kind": kind, "plaintext_bytes": count, "plaintext_sha256": digest}


def _backup(config):
    """Run one backup set in the admitted backup profile and record it."""
    from .backup import run_backup
    from .backup_boundaries import admit_backup_service
    from .operator_commands import configure_operator_database
    from .runtime_paths import RuntimeLayout
    from .startup_interlock import StartupLease

    if config is None:
        raise ConfigError("Explicit backup configuration is required.")
    configuration = load_deployment(config)
    admit_backup_service(configuration)
    # Shared, like the online services: a backup never overlaps offline work,
    # and offline work never starts under a running backup.
    with StartupLease(RuntimeLayout(configuration).interlock, offline=False):
        configure_operator_database(configuration)
        from .jobs.backup_models import BackupRun
        from .runtime_database import require_current_schema

        require_current_schema()
        recorded = {}

        def record(**facts):
            """Persist the run and keep its digest for the operator's notes."""
            recorded.update(facts)
            BackupRun.objects.create(**facts)

        manifest = run_backup(configuration, record=record)
    # The manifest digest is what the operator records off the host and
    # compares at restore, since the sealed files alone prove no origin.
    return {
        "backup_recorded": True,
        "database_bytes": manifest["database"]["plaintext_bytes"],
        "files_bytes": manifest["files"]["plaintext_bytes"],
        "recipient_fingerprint": manifest["recipient_fingerprint"],
        "manifest_digest": recorded["manifest_digest"],
    }


def execute_backup_command(args):
    """Dispatch the three backup commands with one generic refusal each."""
    configure_logging()
    try:
        if args.command == "backup-keygen":
            result = _keygen(args.destination)
        elif args.command == "backup-open":
            result = _open(args.key, args.input, args.destination)
        else:
            result = _backup(args.config)
    except Exception as error:
        emit_failure(error, event=Event.STARTUP_REJECTED)
        print(
            {
                "backup-keygen": "ERROR: key generation refused; use a new "
                "owner-only destination outside the runtime root",
                "backup-open": "ERROR: the sealed backup could not be opened; "
                "check the key and the file and use a new destination",
            }.get(
                args.command,
                "ERROR: backup refused or incomplete; check the backup profile's "
                "mounts, recipient key and database access",
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0
