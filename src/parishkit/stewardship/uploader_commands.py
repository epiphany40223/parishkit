"""The off-site uploader's ``upload`` command (#235).

Operations specification, "Dedicated off-site uploader": the one-shot
``backup-uploader`` profile copies finished, sealed backup sets to the
configured Google Drive folder, so the backup profile, which reads every
tree, has no route out (since the contract release it never copies). The
copy itself is ``backup_offsite``'s (the same budgets, retries, deadline,
outcome rows, alert and Drive retention); what is the uploader's
own is the order in which it acts, which follows request mode's idle
pattern so a five-minute cron line leaves no trace when there is nothing to
do ("Scheduling"):

1. It admits its mounts and takes the host's copy lock before it connects
   to the database. A cron run that finds the lock held exits silently;
   ``--wait-lock SECONDS`` waits, and reports ``busy`` (and logs a
   ``lock_timeout`` entry with the limit and the time waited) when the wait
   expires.
2. It checks the schema and reads the destination (the definer
   ``stewardship_backup_destination_v1()``) and the pending sets, without
   the startup interlock. A database it cannot reach, or one not on the
   current schema, is a silent exit; one that refuses the login logs one
   WARNING.
3. Only with something to do does it take the interlock shared, admit its
   login and check the schema again; it releases the interlock before any
   upload.

Which sets are pending is decided by ``uploader_pending`` ("Handoff"): a
set must be bound to a backup run row by its name and the database's
clock, and be newer than the newest copy already recorded for the folder.

Output: in cron form (neither option) one JSON line only when the pass
recorded an outcome; with ``--wait-lock`` or ``--require-digest`` always
one JSON line. ``--require-digest D`` (upgrade step 1) adds
``required_copy``: whether an ``uploaded`` row exists for that manifest
digest and the current folder after the pass, whichever run wrote it.
"""

import json
import logging
import re
import sys
import time
from datetime import UTC
from functools import partial
from pathlib import Path

from parishkit.config import ConfigError

from .backup import CLOCK_SKEW, REQUIRED_WITHIN, set_started
from .backup_offsite import (
    CATCH_UP_SETS,
    COPY_SECONDS,
    CopyLockTimeout,
    _complete_sets,
    _copy_lock,
    _copy_sets,
    _digest,
    _failed,
    _newest_state,
    _record,
)
from .deployment import load_deployment
from .observability import Event, FailureKind, configure_logging, emit, emit_failure

DIGEST = re.compile(r"[0-9a-f]{64}")
# The longest copy-lock wait an operator or the upgrade may ask for: as long
# as the copy budget, which bounds any copy holding the lock.
MAX_WAIT_SECONDS = COPY_SECONDS


class Quiet(Exception):
    """The pass found nothing it may do now; a cron run exits 0 silently.

    ``state`` is what an explicit run (``--wait-lock`` or
    ``--require-digest``) reports instead.
    """

    def __init__(self, state):
        super().__init__(state)
        self.state = state


def destination():
    """Return ``(folder_id, subject)`` from the destination definer, or None.

    The definer (frozen files 0176 and 0193) returns the applied backup
    folder link and the delegated mailbox, each null when not configured,
    so the uploader never reads the configuration document. A saved link
    that no longer parses is a configuration error, not "off", as in
    ``backup_offsite.destination_from``.
    """
    from django.db import connection

    from .backup_drive import folder_id_from_url

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT backup_target, delegated_email "
            "FROM public.stewardship_backup_destination_v1()"
        )
        row = cursor.fetchone()
    if row is None or row[0] is None or row[1] is None:
        return None
    try:
        return folder_id_from_url(row[0]), row[1]
    except ValueError:
        raise ConfigError("The off-site backup folder link is invalid.") from None


def binds(name, started_at, completed_at):
    """Whether one backup run row binds a set's directory name.

    The name must be the row's ``started_at`` to the second in UTC, and the
    row's ``completed_at`` (the database's own ``STATEMENT_TIMESTAMP()``)
    at most ``REQUIRED_WITHIN`` after it, so a set named for another day
    than the database recorded it on is refused (#235, "Names bound to the
    database's clock"). The table's check keeps ``completed_at`` from being
    earlier than ``started_at``.
    """
    return (
        started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ") == name
        and completed_at - started_at <= REQUIRED_WITHIN
    )


def database_time():
    """The database's current time, the clock every run row was bound to."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp()")
        return cursor.fetchone()[0]


def uploader_pending(backups, folder_id, *, future=None):
    """The uploader's sets to copy now, as ``(directory, digest, refusal)``.

    Of the newest ``CATCH_UP_SETS`` complete local sets (#235, "Handoff"):

    - a set whose name is not newer than the newest set with an ``uploaded``
      row for this folder is not pending (no row, no interlock), so a staged
      set can neither become an earlier day's retention anchor nor reuse a
      copied set's name. Copies named more than ``CLOCK_SKEW`` after the
      database's time (a clock that jumped) are left out of that comparison
      and their names added to ``future``;
    - a set whose digest already has an ``uploaded`` row here is already
      copied and skipped;
    - a set with no digest (its manifest is a link, not a regular file with
      one link, past the cap or unreadable) or whose digest has no backup
      run row at all is not considered, silently: a stray directory (a hand
      copy, a half-restored set) or one a compromised backup image staged.
      Neither can be a run's set, and refusing it with a row and a warning
      on every five-minute pass would let anything that writes the backups
      directory raise an alert at will. A real set damaged this way still
      shows: its backup run then has no copy row, which the off-site alert
      reports once its grace has passed (``backup_health``);
    - a set whose rows all fail ``binds`` is pending with the refusal
      ``verification``, which the copy records and logs. Any one binding
      row among several with the digest admits the set.
    """
    from .jobs.backup_models import BackupRun, BackupUpload

    limit = database_time() + CLOCK_SKEW
    copies = BackupUpload.objects.filter(state="uploaded", folder_id=folder_id)
    copied, names = set(), []
    for name, digest in copies.values_list("set_name", "manifest_digest"):
        copied.add(digest)
        started = set_started(name or "")
        if started is None:
            continue
        if started > limit:
            if future is not None and name not in future:
                future.append(name)
        else:
            names.append(name)
    newest = max(names, default=None)
    pending = []
    for directory in _complete_sets(backups)[-CATCH_UP_SETS:]:
        if newest is not None and directory.name <= newest:
            continue
        digest = _digest(directory)
        if digest is None or digest in copied:
            continue
        runs = BackupRun.objects.filter(manifest_digest=digest).values_list(
            "started_at", "completed_at"
        )
        if not runs:
            continue
        refusal = None
        if not any(binds(directory.name, *times) for times in runs):
            refusal = "verification"
        pending.append((directory, digest, refusal))
    return pending


def _required(digest, folder_id):
    """Whether an ``uploaded`` row exists for ``digest`` in this folder."""
    from .jobs.backup_models import BackupUpload

    copied = BackupUpload.objects.filter(
        state="uploaded", manifest_digest=digest, folder_id=folder_id
    ).exists()
    return "uploaded" if copied else "not_uploaded"


def _read_quietly(read):
    """Run one read of step 2 and turn the quiet failures into ``Quiet``.

    A database that is not ready (offline work, a restart) or not on this
    image's schema is a silent exit; one that refuses the login (a missed
    ``database-grants``, a source rule not in force) or the read logs one
    WARNING first, so the cron output shows it.
    """
    from django.db import DatabaseError

    from .runtime_database import database_not_ready

    try:
        return read()
    except DatabaseError as error:
        if not database_not_ready(error.__cause__ or error):
            emit_failure(error, event=Event.STARTUP_REJECTED, level=logging.WARNING)
        raise Quiet("unavailable") from None


def _schema_current():
    """Raise ``Quiet`` unless the database is on this image's schema."""
    from .runtime_database import require_current_schema

    try:
        require_current_schema()
    except ConfigError:
        raise Quiet("schema") from None


def run_upload(
    configuration,
    *,
    wait_seconds=None,
    require_digest=None,
    session_factory=None,
    sleep=time.sleep,
    clock=time.monotonic,
):
    """One uploader pass; returns the summary to print, or None for silence.

    The caller has admitted the mounts and configured the database. See the
    module docstring for the order of the steps. ``Quiet`` stops are silent
    in cron form and reported by state in explicit form; any other failure
    is raised for the command's generic refusal.
    """
    explicit = wait_seconds is not None or require_digest is not None
    try:
        return _upload_pass(
            configuration,
            wait_seconds=wait_seconds,
            require_digest=require_digest,
            session_factory=session_factory,
            sleep=sleep,
            clock=clock,
        )
    except Quiet as stop:
        return {"state": stop.state} if explicit else None


def _upload_pass(
    configuration, *, wait_seconds, require_digest, session_factory, sleep, clock
):
    """The pass itself; raises ``Quiet`` for every stop with nothing done."""
    from .backup_drive import log_timeout, workspace_session
    from .mail_catcher import admit_real_workspace
    from .runtime_paths import RuntimeLayout, explicit_path, private_directory
    from .startup_interlock import StartupBusy, StartupLease
    from .uploader_boundaries import admit_uploader_identity

    explicit = wait_seconds is not None or require_digest is not None
    try:
        # Where a real Workspace key may not be used (#1399, in LOCAL
        # without a mail redirect, or under the fake clock), nothing is read
        # and nothing is copied.
        admit_real_workspace(configuration.profile, configuration.mail_redirect)
    except ConfigError:
        result = {"state": "not_configured"}
        if require_digest is not None:
            result["required_copy"] = "not_configured"
        return result if explicit else None
    backups = private_directory(explicit_path(configuration.paths["backups"]))
    lock = _copy_lock(backups, wait_seconds=wait_seconds, clock=clock, sleep=sleep)
    try:
        with lock as held:
            if not held:
                raise Quiet("busy")
            return _locked_pass(
                configuration,
                backups,
                explicit=explicit,
                require_digest=require_digest,
                session_factory=session_factory
                or partial(
                    workspace_session,
                    profile=configuration.profile,
                    redirect=configuration.mail_redirect,
                ),
                sleep=sleep,
                clock=clock,
                lease=StartupLease(
                    RuntimeLayout(configuration).interlock, offline=False
                ),
                busy=StartupBusy,
                admit=admit_uploader_identity,
            )
    except CopyLockTimeout as expired:
        # Raised only while the lock is taken. The durable entry goes
        # through the private timeout connection, which the log writer
        # admits for this login's timeout events (0193).
        log_timeout(
            "lock_timeout",
            limit_seconds=expired.limit_seconds,
            elapsed_seconds=max(1, expired.elapsed_seconds),
        )
        raise Quiet("busy") from None


def _locked_pass(
    configuration,
    backups,
    *,
    explicit,
    require_digest,
    session_factory,
    sleep,
    clock,
    lease,
    busy,
    admit,
):
    """Steps 2 and 3 under the copy lock; see ``run_upload``."""
    from django.db import connection

    future = []
    _read_quietly(_schema_current)
    target = _read_quietly(destination)
    if target is None:
        # Record once that copies stopped on purpose, so an old failure no
        # longer alerts once the destination is gone.
        record_disabled = _read_quietly(_newest_state) not in {None, "disabled"}
        pending = []
    else:
        record_disabled = False
        pending = _read_quietly(
            partial(uploader_pending, backups, target[0], future=future)
        )
    if not pending and not record_disabled:
        if not explicit:
            raise Quiet("idle")
        result = {"state": "not_configured"}
        if target is not None:
            result = {"state": "uploaded", "sets": 0}
        return _with_required(result, require_digest, target)
    # Something to do: the interlock (shared, as every online service holds
    # it) for the identity and schema checks, released before any upload.
    try:
        lease.__enter__()
    except busy:
        raise Quiet("busy") from None
    try:
        admit()
        _schema_current()
        if record_disabled:
            _record("disabled")
    finally:
        lease.__exit__(None, None, None)
    if target is None:
        return _with_required({"state": "not_configured"}, require_digest, None)
    if future:
        # A clock that jumped left a copy named in the future on record; it
        # is ignored for "newer than the newest copy" (#235). The names go
        # in the command's output; the process log takes no free text.
        emit(
            Event.TASK_FAILED,
            level=logging.WARNING,
            failure_kind=FailureKind.BACKUP_OFFSITE_FUTURE_COPY,
        )
    folder_id, subject = target
    connection.close()
    try:
        result = _copy_sets(
            backups,
            configuration,
            folder_id,
            subject,
            session_factory=session_factory,
            sleep=sleep,
            clock=clock,
            deadline=clock() + COPY_SECONDS,
            pending_sets=partial(uploader_pending, backups, folder_id),
            key=configuration.secrets["google_workspace"],
        )
    except Exception as error:
        # Anything outside the Drive categories (an unreadable local file, a
        # malformed provider reply) still records a failure, so the alert
        # fires and the pages stop showing the previous success.
        emit_failure(error, event=Event.TASK_FAILED)
        result = _failed(folder_id, None, None, "unexpected")
    if future:
        result["future_copies"] = sorted(future)
    return _with_required(result, require_digest, target)


def _with_required(result, require_digest, target):
    """Add ``required_copy`` for ``--require-digest`` (upgrade step 1)."""
    if require_digest is not None:
        result["required_copy"] = (
            "not_configured" if target is None else _required(require_digest, target[0])
        )
    return result


def _wait_seconds(value):
    """``--wait-lock``'s whole seconds, from 1 to ``MAX_WAIT_SECONDS``."""
    if value is None:
        return None
    if not re.fullmatch(r"[1-9][0-9]{0,5}", value) or int(value) > MAX_WAIT_SECONDS:
        raise ConfigError(
            f"--wait-lock takes whole seconds from 1 to {MAX_WAIT_SECONDS}."
        )
    return int(value)


def _upload(args):
    """Load, admit and configure the uploader, then run one pass."""
    from .deployment import ServiceRole
    from .operator_commands import configure_operator_database
    from .uploader_boundaries import admit_uploader_service

    wait_seconds = _wait_seconds(args.wait_lock)
    digest = args.require_digest
    if digest is not None and not DIGEST.fullmatch(digest):
        raise ConfigError("--require-digest takes a manifest's 64-hex SHA-256.")
    if args.config is None:
        raise ConfigError("Explicit uploader configuration is required.")
    configuration = load_deployment(Path(args.config))
    if configuration.service_role is not ServiceRole.BACKUP_UPLOADER:
        raise ConfigError("Run upload in the off-site uploader profile.")
    admit_uploader_service(configuration)
    configure_operator_database(configuration)
    return run_upload(configuration, wait_seconds=wait_seconds, require_digest=digest)


def execute_upload(args):
    """Console entry: one JSON line, silence, or one generic refusal."""
    configure_logging()
    try:
        result = _upload(args)
    except Exception as error:
        emit_failure(error, event=Event.STARTUP_REJECTED)
        print(
            "ERROR: off-site upload refused or incomplete; check the uploader "
            "profile's mounts, its database login and the off-site folder",
            file=sys.stderr,
        )
        return 2
    if result is not None:
        print(json.dumps(result, sort_keys=True))
    return 0
