"""Console entry points for the v1 backup: run it, make its key, open a set.

Every refusal is one fixed sentence, and the process log records only a
reviewed failure category, never paths, keys or database error text.
`backup` runs in the rendered backup profile beside the online services;
`backup-keygen` and `backup-open` run wherever the operator keeps the private
key, which is never the host.
"""

import json
import logging
import os
import sys
from pathlib import Path

from parishkit.config import ConfigError

from .backup_sealing import fingerprint, generate_keypair, load_private, open_sealed
from .deployment import load_deployment
from .observability import Event, FailureKind, configure_logging, emit, emit_failure


def _keygen(destination):
    """Write the private key owner-only to a new file; print the public half.

    The printed fingerprint is the one every backup sealed to this key
    reports, so the operator can record it with the private key and later
    see that the installed public key is the kept pair's.
    """
    if destination is None:
        raise ConfigError("An explicit private key destination is required.")
    private, public = generate_keypair()
    descriptor = os.open(
        Path(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as stream:
        stream.write(private)
    return {
        "public_key": public.strip(),
        "recipient_fingerprint": fingerprint(load_private(destination).public_key),
    }


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
    # Opening succeeded, so this key is the set's recipient: the drill
    # records the fingerprint as proof that the kept key opens the backups.
    return {
        "kind": kind,
        "plaintext_bytes": count,
        "plaintext_sha256": digest,
        "recipient_fingerprint": fingerprint(private.public_key),
    }


def _admit_backup_identity():
    """The session is the backup login with exactly the attributes it was given.

    Like every other process that opens the database, the command proves its
    identity before acting: a rendered document naming another login must not
    dump or record under it. The backup login alone bypasses row-level
    security and belongs to pg_read_all_data; everything else is refused.
    """
    from django.db import connection

    from .database_provisioning import READER_MEMBERSHIP
    from .runtime_database import require_no_temporary_authority

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_user,session_user,rolsuper,rolbypassrls,rolcreatedb,"
            "rolcreaterole,rolreplication,rolinherit,"
            "(SELECT string_agg(m.rolname||':'||am.inherit_option::text||':'"
            "||am.admin_option::text,',' ORDER BY m.rolname) FROM pg_auth_members am "
            "JOIN pg_roles m ON m.oid=am.roleid WHERE am.member=r.oid) "
            "FROM pg_roles r WHERE rolname=current_user"
        )
        row = cursor.fetchone()
    login = "pk_stewardship_backup_worker"
    attributes = (False, True, False, False, False, False)  # super, bypass, ...
    if row != (login, login, *attributes, READER_MEMBERSHIP):
        raise ConfigError("The backup command requires its own database login.")
    require_no_temporary_authority()


def recipient_changed(current):
    """Warn when this run seals to a different key than the previous run did.

    A replaced ``backup_data`` file, or a public key that is not the kept
    private key's pair, still backs up without complaint; only a restore
    would notice. A change is legitimate only when the operator installed a
    new key on purpose, so it is logged as a WARNING for them to confirm.
    Returns whether the key changed.
    """
    from .jobs.backup_models import BackupRun

    previous = (
        BackupRun.objects.order_by("-completed_at")
        .values_list("recipient_fingerprint", flat=True)
        .first()
    )
    changed = previous is not None and previous != current
    if changed:
        emit(
            Event.CONFIG_MISMATCH,
            level=logging.WARNING,
            failure_kind=FailureKind.BACKUP_RECIPIENT_CHANGED,
        )
    return changed


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

        _admit_backup_identity()
        require_current_schema()
        recorded = {}

        def record(**facts):
            """Persist the run and keep its digest for the operator's notes."""
            recorded.update(
                facts,
                recipient_changed=recipient_changed(facts["recipient_fingerprint"]),
            )
            BackupRun.objects.create(**facts)

        manifest = run_backup(configuration, record=record)
    # The off-site copy runs after the lease: it reads only the finished set
    # and appends its outcome, so a slow upload never holds offline work back.
    offsite = _copy_offsite(configuration)
    # The manifest digest is what the operator records off the host and
    # compares at restore, since the sealed files alone prove no origin.
    return {
        "backup_recorded": True,
        "database_bytes": manifest["database"]["plaintext_bytes"],
        "files_bytes": manifest["files"]["plaintext_bytes"],
        "recipient_fingerprint": manifest["recipient_fingerprint"],
        "recipient_changed": recorded["recipient_changed"],
        "manifest_digest": recorded["manifest_digest"],
        "offsite": offsite,
    }


def _copy_offsite(configuration):
    """Copy the new set off-site; a failure never undoes the local backup.

    Drive failures are recorded and categorized by ``copy_offsite`` itself;
    anything unexpected is logged by category here and reported as failed,
    and the scheduler's alert and the next run take it from there.
    """
    from .backup_offsite import copy_offsite

    try:
        return copy_offsite(configuration)
    except Exception as error:
        emit_failure(error, event=Event.TASK_FAILED)
        return {"state": "failed", "failure_kind": "unexpected"}


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
