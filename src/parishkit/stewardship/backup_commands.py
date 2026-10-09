"""Console entry points for the v1 backup: run it, make its key, open a set.

Every refusal is one fixed sentence, and the process log records only a
reviewed failure category, never paths, keys or database error text.
`backup` runs in the rendered backup profile beside the online services;
`backup-keygen`, `backup-open` and `backup-prove` run wherever the operator
keeps the private key, which is never the host.
"""

import json
import logging
import os
import sys
from pathlib import Path

from parishkit.config import ConfigError

from .backup_sealing import (
    MAX_PROOF_TEXT,
    display_code,
    fingerprint,
    generate_keypair,
    load_private,
    open_proof,
    open_sealed,
)
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


def _prove(key, source):
    """Print the code a portal challenge hides, proving this key opens it.

    The Admin portal's **Backup encryption key** page seals a short code to
    the public key being installed; typing the code back proves the
    Administrator holds its private key before any backup is sealed to it.
    The challenge comes from a file, or from standard input when no file is
    named, so it can be pasted into ``docker run -i``.
    """
    if key is None:
        raise ConfigError("An explicit private key file is required.")
    private = load_private(key)
    if source is None:
        text = sys.stdin.readline(MAX_PROOF_TEXT + 2)
    else:
        with Path(source).open(encoding="ascii") as stream:
            text = stream.readline(MAX_PROOF_TEXT + 2)
    return {
        "code": display_code(open_proof(text, private)),
        "recipient_fingerprint": fingerprint(private.public_key),
    }


def _admit_backup_identity():
    """The session is the backup login with exactly the attributes it was given.

    Like every other process that opens the database, the command proves its
    identity before acting: a rendered document naming another login must not
    dump or record under it. The backup login alone bypasses row-level
    security and belongs to pg_read_all_data; everything else is refused,
    including the isolation drift every login is checked for (no member
    roles, no owned objects, no foreign-data USAGE, no parameter privilege).

    Its grants are checked too (#389 L7), as far as pg_read_all_data leaves
    anything to check: every write privilege must be one ``runtime_grants``
    declares, and it may not execute a definer routine, use or update a
    sequence, or create in a schema or the database.
    """
    from django.db import connection

    from .database_provisioning import ISOLATION_DRIFT, READER_MEMBERSHIP
    from .runtime_database import require_no_temporary_authority

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_user,session_user,rolsuper,rolbypassrls,rolcreatedb,"
            "rolcreaterole,rolreplication,rolinherit,"
            "(SELECT string_agg(m.rolname||':'||am.inherit_option::text||':'"
            "||am.admin_option::text,',' ORDER BY m.rolname) FROM pg_auth_members am "
            "JOIN pg_roles m ON m.oid=am.roleid WHERE am.member=r.oid),"
            f"{', '.join(ISOLATION_DRIFT.values())} "
            "FROM pg_roles r WHERE rolname=current_user"
        )
        row = cursor.fetchone()
    login = "pk_stewardship_backup_worker"
    # Each column the login must show, and what a refusal calls a mismatch.
    expected = {
        "not the backup login": login,
        "not its own session": login,
        "superuser": False,
        "does not bypass row-level security": True,
        "may create databases": False,
        "may create roles": False,
        "replication": False,
        "inherits role authority": False,
        "role membership other than pg_read_all_data": READER_MEMBERSHIP,
        **dict.fromkeys(ISOLATION_DRIFT, False),
    }
    if row is None:
        raise ConfigError("The backup command requires its own database login.")
    if failed := [
        name
        for (name, value), actual in zip(expected.items(), row, strict=True)
        if actual != value
    ]:
        # Names only which checks failed, so an operator can repair the role.
        raise ConfigError(
            f"The backup command requires its own database login: {', '.join(failed)}."
        )
    _admit_backup_grants(connection)
    require_no_temporary_authority()


def _admit_backup_grants(connection):
    """Refuse a backup login whose grants exceed what the backup declares.

    pg_read_all_data gives SELECT on every table and sequence and USAGE on
    every schema, so only what it does not give is compared: table and
    column write privileges against ``runtime_grants``, and the definer,
    sequence-write and CREATE authority of ``excess_authority``.
    """
    from .accounts.credential_database import excess_authority
    from .database_provisioning import BACKUP_SEQUENCES
    from .deployment import ServiceRole
    from .runtime_grants import runtime_grants

    tables, columns = runtime_grants(ServiceRole.BACKUP_WORKER)
    allowed = {table: set(grants) for table, grants in tables.items()}
    for table, grants in columns.items():
        allowed.setdefault(table, set()).update(grants)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT n.nspname,c.relname,p FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "CROSS JOIN unnest(ARRAY['INSERT','UPDATE','DELETE','TRUNCATE',"
            "'REFERENCES','TRIGGER']) p "
            "WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' "
            "AND c.relkind IN('r','p','v','m','f') "
            "AND (has_table_privilege(current_user,c.oid,p) OR "
            "CASE WHEN p IN('INSERT','UPDATE','REFERENCES') "
            "THEN has_any_column_privilege(current_user,c.oid,p) ELSE false END)"
        )
        for schema, table, privilege in cursor.fetchall():
            if schema != "public" or privilege not in allowed.get(table, set()):
                raise ConfigError(
                    "The backup login's database grants are excessive: "
                    f"{privilege} on {schema}.{table}."
                )
        if excess := excess_authority(cursor, sequences=BACKUP_SEQUENCES):
            raise ConfigError(
                "The backup login's database grants are excessive: "
                f"{', '.join(excess)}."
            )


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
    from .backup import configured_recipient, run_backup
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
        from django.db import connection
        from django.db.migrations.recorder import MigrationRecorder

        # The set the schema check just admitted, for the manifest (#608).
        applied = MigrationRecorder(connection).applied_migrations()
        recorded = {}

        def record(**facts):
            """Persist the run and keep its digest for the operator's notes."""
            recorded.update(
                facts,
                recipient_changed=recipient_changed(facts["recipient_fingerprint"]),
            )
            BackupRun.objects.create(**facts)

        # Read before the run starts, so the recorded start time follows the
        # configuration the key came from (see backup_health.key_changed).
        configured = configured_recipient()
        manifest = run_backup(
            configuration, record=record, migrations=applied, recipient=configured
        )
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
        # "configured" when an Administrator set the key in the portal,
        # "file" when the installed backup_data key was used.
        "recipient_source": "file" if configured is None else "configured",
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
    """Dispatch the four backup commands with one generic refusal each."""
    configure_logging()
    try:
        if args.command == "backup-keygen":
            result = _keygen(args.destination)
        elif args.command == "backup-open":
            result = _open(args.key, args.input, args.destination)
        elif args.command == "backup-prove":
            result = _prove(args.key, args.input)
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
                "backup-prove": "ERROR: the challenge could not be opened; check "
                "that the key is the private key for the public key you pasted "
                "and that the whole challenge line was copied",
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
