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
import re
import sys
from pathlib import Path

from parishkit import __version__
from parishkit.config import ConfigError

from .backup import MANIFEST
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


_SAFE_TEXT = re.compile(r"[A-Za-z0-9._:/@+-]{1,200}")  # used with fullmatch


def _other_release_hint(source):
    """Name the release that took a set when it is not the running one (#1580).

    A sealed file carries no release, but the set's plain ``manifest.json``
    sits beside it. When that manifest names a different ``application_version``,
    a failed open is most likely an image mismatch, so the refusal says which
    release image to use. Anything unreadable or odd yields no hint, and only
    short, plainly-shaped values are echoed, never paths or file content.
    """
    try:
        with (Path(source).parent / MANIFEST).open("rb") as handle:
            # A real manifest is a few hundred bytes; the cap keeps a huge
            # or endless file from stalling the refusal.
            manifest = json.loads(handle.read(65536).decode("utf-8"))
        version = manifest["application_version"]
        image = manifest.get("image")
    except Exception:  # best-effort hint: any failure (even RecursionError) means none
        return ""
    if not isinstance(version, str) or not _SAFE_TEXT.fullmatch(version):
        return ""
    if version == __version__:
        return ""
    text = (
        f" This set was taken by release {version}, and a set is opened with"
        f" that set's own release image, not release {__version__}"
    )
    if isinstance(image, str) and _SAFE_TEXT.fullmatch(image):
        text += f"; use {image}"
    return text + "."


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


def reusable_upgrade_backup():
    """The pre-upgrade backup a repeated users-upgrade deploy reuses, or None.

    A deploy of the users release (#952) whose ``migrate
    --users-upgrade-base`` stopped partway (a failure, a time limit) leaves
    migrations outside the users swap pending, which this image's backup
    refuses, so the repeated run's step-1 backup could never run. That
    state can only come from the earlier run of this same upgrade: every
    online service refuses a pending migration, so nothing but that
    migration has written to the database since the earlier run's step-1
    backup, which therefore is still its restore point (with the previous
    release, as for any failed schema upgrade).

    Returns that backup's facts when, and only when: nothing applied is
    unknown to this image; the users swap is still pending; something
    outside its closure is pending too (only the swap pending means a fresh
    backup runs, as before); everything pending is the swap's closure or
    the base migration's plan (``users_upgrade_targets`` admits the graph);
    and the newest backup is within the window ``migrate`` itself requires,
    so the repeated base migration is admitted on it. Its off-site state is
    the recorded copy of that very set.
    """
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    from .backup import recent_backup_recorded
    from .backup_offsite import destination
    from .jobs.backup_models import BackupRun, BackupUpload
    from .operator_commands import users_upgrade_targets
    from .runtime_database import USERS_SWAP, users_swap_closure

    executor = MigrationExecutor(connection)
    loader = executor.loader
    loader.check_consistent_history(connection)
    if set(loader.applied_migrations) - set(loader.disk_migrations):
        return None
    if USERS_SWAP in loader.applied_migrations:
        return None
    closure = users_swap_closure(loader.graph)
    plan = executor.migration_plan(loader.graph.leaf_nodes())
    pending = {(migration.app_label, migration.name) for migration, _ in plan}
    base = pending - closure
    if not closure or not base:
        return None
    # Refuses a graph whose base no single migrate target per app reaches.
    users_upgrade_targets(loader)
    with connection.cursor() as cursor:
        if not recent_backup_recorded(cursor):
            return None
    # Only the columns read here: a half-migrated database may have
    # changed others, which a whole-row read would trip over.
    run = (
        BackupRun.objects.order_by("-completed_at")
        .values("completed_at", "manifest_digest", "application_version")
        .first()
    )
    copied = BackupUpload.objects.filter(
        state="uploaded", manifest_digest=run["manifest_digest"]
    ).exists()
    if copied:
        offsite = "uploaded"
    elif destination() is None:
        offsite = "not_configured"
    else:
        offsite = "not_uploaded"
    return {
        "backup_recorded": False,
        "backup_reused": True,
        "reused_backup": {
            "completed_at": run["completed_at"].isoformat(),
            "manifest_digest": run["manifest_digest"],
            "application_version": run["application_version"],
        },
        "pending_base_migrations": len(base),
        "offsite": {"state": offsite},
    }


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
# The migration that creates the request record (frozen file 0062).
BACKUP_REQUESTS = ("stewardship_jobs", "0023_backup_request")


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
    from django.db import DatabaseError, connection
    from django.db.migrations.recorder import MigrationRecorder

    from .backup import configured_recipient, run_backup
    from .backup_requests import claim, held, settle_lapsed, waiting
    from .jobs.backup_models import BackupRun

    if not _lock_backups(wait=wait):
        raise Quiet
    # The set the schema check just admitted, for the manifest (#608).
    applied = MigrationRecorder(connection).applied_migrations()
    request = None
    # The request record is in the users swap's closure, so a scheduled
    # backup of a users upgrade stopped before the swap (#952) has no
    # request table to read; request mode admits only a current schema.
    if BACKUP_REQUESTS in applied:
        settle_lapsed()
        request = waiting()
    if request_mode:
        # Every quiet stop after taking the lock releases it, as the refused
        # claim below does, rather than leaving it to the connection's end.
        if request is None:
            _unlock_backups()
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
        manifest = run_backup(
            configuration, record=record, migrations=applied, recipient=configured
        )
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


def _backup(config, *, request_mode=False, users_upgrade_resume=False):
    """Run one backup set in the admitted backup profile and record it.

    ``request_mode`` is the host cron's five-minute poll for Take a backup
    now (ADM-13 PR 3): it reads for a waiting request before it takes any
    lock, and returns None (exit 0, no output, no log line) when there is
    none, the database cannot be read, another backup holds the lock,
    offline work holds the startup interlock, or a bulk send holds it.

    ``users_upgrade_resume`` (the scripted upgrade's repeated users-upgrade
    run only) reports the earlier run's backup instead when the users
    upgrade's base migration stopped partway (``reusable_upgrade_backup``);
    in every other state it backs up as usual. The two modes never combine.
    """
    from django.db import DatabaseError, OperationalError

    from .backup_boundaries import admit_backup_service
    from .operator_commands import configure_operator_database
    from .runtime_paths import RuntimeLayout
    from .startup_interlock import StartupBusy, StartupLease

    if config is None:
        raise ConfigError("Explicit backup configuration is required.")
    if request_mode and users_upgrade_resume:
        raise ConfigError("--request and --users-upgrade-resume do not combine.")
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
        if users_upgrade_resume:
            reused = reusable_upgrade_backup()
            if reused is not None:
                # The scripted upgrade logs this line in the run's log and
                # in stewardship-backup.log, as it does every backup's.
                return reused
        # The users upgrade (#952) may leave only the users swap pending: a
        # deploy that stopped after its base migration (no conversion
        # inputs, a refused conversion, a failed final migration, an
        # interruption) is repeated, and the repeat's step 1 backs up first.
        # That database is a valid restore point (its manifest lists exactly
        # what is applied), so a scheduled backup admits it; anything else
        # pending is still refused. Request mode admits only a current
        # schema: the request record is itself in the swap's closure.
        require_current_schema(users_swap_pending=not request_mode)
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
                args.config,
                request_mode=bool(getattr(args, "request", None)),
                users_upgrade_resume=bool(getattr(args, "users_upgrade_resume", None)),
            )
            if result is None:
                # Request mode with nothing to do: silent, so the five-minute
                # cron poll leaves no trace.
                return 0
    except Exception as error:
        emit_failure(error, event=Event.STARTUP_REJECTED)
        hint = _other_release_hint(args.input) if args.command == "backup-open" else ""
        print(
            {
                "backup-keygen": "ERROR: key generation refused; use a new "
                "owner-only destination outside the runtime root",
                "backup-open": "ERROR: the sealed backup could not be opened; "
                "check the key and the file and use a new destination."
                + (hint or " A set is opened with that set's own release image."),
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
