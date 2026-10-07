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


# The PostgreSQL session advisory lock every backup run holds while it makes
# its set (ADM-13 PR 3), so a requested backup and a scheduled one never make
# sets at the same time; the off-site upload afterwards is not covered.
# Request mode only tries it; a scheduled run waits for it, for at most
# SCHEDULED_LOCK_SECONDS. Any database login could take this key and stall
# backups; a stalled scheduled run then records its timeout and exits 2, and
# the overdue-backup alert fires (accepted on #530 over a new lock file,
# which every host would need provisioned and mounted).
BACKUP_LOCK = (736241, 1)
SCHEDULED_LOCK_SECONDS = 1800


class Quiet(Exception):
    """Request mode found nothing it may do now; exit 0 with no output."""


def _lock_backups(*, wait):
    """Take the backup lock on this connection; return whether it was taken.

    Without ``wait`` it only tries. With it, it waits at most
    SCHEDULED_LOCK_SECONDS and records a timeout (#287: the limit, its
    seconds and the time waited) before refusing.
    """
    import time

    from django.db import OperationalError, connection

    with connection.cursor() as cursor:
        if not wait:
            cursor.execute("SELECT pg_try_advisory_lock(%s,%s)", BACKUP_LOCK)
            return cursor.fetchone()[0]
        started = time.monotonic()
        cursor.execute(f"SET lock_timeout='{SCHEDULED_LOCK_SECONDS}s'")
        try:
            cursor.execute("SELECT pg_advisory_lock(%s,%s)", BACKUP_LOCK)
        except OperationalError as error:
            if getattr(error.__cause__, "sqlstate", None) != "55P03":
                raise
            from .audit.timeouts import record_timeout

            record_timeout(
                Event.TASK_TIMED_OUT,
                what="lock_timeout",
                level="WARNING",
                limit_seconds=SCHEDULED_LOCK_SECONDS,
                elapsed_seconds=max(1, round(time.monotonic() - started)),
                bind_task=False,
            )
            raise ConfigError("Another backup held the backup lock too long.") from None
        finally:
            cursor.execute("RESET lock_timeout")
        return True


def _run(configuration, *, wait, request_mode):
    """Run one backup set under the backup lock and settle a request with it.

    The caller holds the startup interlock and has admitted the backup
    login. A scheduled run (``wait``) takes the lock however long another
    backup holds it (up to its limit) and then completes any waiting
    request too, even during a bulk send. Request mode (``request_mode``)
    runs only for a waiting request, stamps it held during a bulk Family
    send, and raises ``Quiet`` when the lock is taken or nothing is waiting.
    """
    from django.db import DatabaseError

    from .backup import configured_recipient, run_backup
    from .backup_requests import claim, held, settle_lapsed, waiting
    from .jobs.backup_models import BackupRun

    if not _lock_backups(wait=wait):
        raise Quiet
    settle_lapsed()
    request = waiting()
    if request_mode:
        if request is None:
            raise Quiet
        from .source.send_hold import family_send_active

        if family_send_active():
            try:
                held(request)
            except (DatabaseError, ConfigError) as error:
                # The same race as the claim below: the request lapsed or
                # moved on since it was read. Say so and stop quietly.
                emit_failure(error, event=Event.TASK_FAILED, level=logging.WARNING)
                _unlock_backups()
            raise Quiet
    if request is not None:
        try:
            claim(request)
        except (DatabaseError, ConfigError) as error:
            # The guard refused the claim (the request lapsed, or a restore
            # review began, since it was read), or the row moved on so the
            # guarded update matched nothing (ConfigError from _move). Say
            # so; request mode then stops, while a scheduled run makes its
            # set without it.
            emit_failure(error, event=Event.TASK_FAILED, level=logging.WARNING)
            if request_mode:
                _unlock_backups()
                raise Quiet from None
            request = None
    recorded = {}

    def record(**facts):
        """Persist the run and keep its digest for the operator's notes."""
        recorded.update(
            facts,
            recipient_changed=recipient_changed(facts["recipient_fingerprint"]),
        )
        recorded["run_id"] = BackupRun.objects.create(**facts).pk

    # Read before the run starts, so the recorded start time follows the
    # configuration the key came from (see backup_health.key_changed).
    configured = configured_recipient()
    try:
        manifest = run_backup(configuration, record=record, recipient=configured)
    except Exception as error:
        if request is not None:
            from .backup_requests import fail

            fail(request, error)
        raise
    if request is not None:
        from .backup_requests import finish

        finish(request, recorded["run_id"])
    # The lock serializes making backup sets; the off-site upload that
    # follows needs no lock, so another backup may start while it runs.
    _unlock_backups()
    return manifest, recorded, configured, request


def _unlock_backups():
    """Release the backup lock this connection holds."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s,%s)", BACKUP_LOCK)


def _backup(config, *, request_mode=False):
    """Run one backup set in the admitted backup profile and record it.

    ``request_mode`` is the host cron's five-minute poll for Take a backup
    now (ADM-13 PR 3): it reads for a waiting request before it takes any
    lock, and returns None (exit 0, no output, no log line) when there is
    none, the database cannot be read, another backup holds the lock,
    offline work holds the startup interlock, or a bulk send holds it.
    """
    from django.db import DatabaseError, OperationalError

    from .backup_boundaries import admit_backup_service
    from .operator_commands import configure_operator_database
    from .runtime_paths import RuntimeLayout
    from .startup_interlock import StartupBusy, StartupLease

    if config is None:
        raise ConfigError("Explicit backup configuration is required.")
    configuration = load_deployment(config)
    admit_backup_service(configuration)
    if request_mode:
        configure_operator_database(configuration)
        from .backup_requests import waiting

        try:
            if waiting() is None:
                return None
        except OperationalError:
            # The database cannot be reached (offline work, a restart): the
            # page's "not picked up" message is what shows a missed poll.
            return None
        except DatabaseError as error:
            # Reached but refused (a missed database-grants step): say so
            # once in the process log, so the cron output shows it.
            emit_failure(error, event=Event.STARTUP_REJECTED, level=logging.WARNING)
            return None
        lease = StartupLease(RuntimeLayout(configuration).interlock, offline=False)
        try:
            lease.__enter__()
        except StartupBusy:
            return None
    else:
        # Shared, like the online services: a backup never overlaps offline
        # work, and offline work never starts under a running backup.
        lease = StartupLease(RuntimeLayout(configuration).interlock, offline=False)
        lease.__enter__()
    try:
        if not request_mode:
            configure_operator_database(configuration)
        from .runtime_database import require_current_schema

        _admit_backup_identity()
        require_current_schema()
        try:
            manifest, recorded, configured, request = _run(
                configuration, wait=not request_mode, request_mode=request_mode
            )
        except Quiet:
            return None
    finally:
        lease.__exit__(None, None, None)
    # The off-site copy runs after the lease: it reads only the finished set
    # and appends its outcome, so a slow upload never holds offline work back.
    offsite = _copy_offsite(configuration)
    # The manifest digest is what the operator records off the host and
    # compares at restore, since the sealed files alone prove no origin.
    result = {
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
    if request is not None:
        result["request_id"] = str(request.pk)
    return result


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
            result = _backup(
                args.config, request_mode=bool(getattr(args, "request", None))
            )
            if result is None:
                # Request mode with nothing to do: silent, so the five-minute
                # cron poll leaves no trace.
                return 0
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
