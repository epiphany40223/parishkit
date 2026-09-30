"""Copy the newest sealed backup sets to the configured Google Drive folder.

The backup command calls :func:`copy_offsite` after a set is complete and
recorded. The destination is the applied configuration's ``backup``
integration (a Drive folder link); the identity is the Google Workspace
integration's delegated mailbox user, and the key is the installed Workspace
credential, which the backup profile already reads (read-only) as part of the
credentials tree it archives. Nothing else about the backup changes: a failed
copy leaves the local set and its record in place, appends a ``failed``
outcome the scheduler alerts on, and the next run tries again.

Only the newest few sets not yet copied to the current folder are attempted,
oldest first, so a long outage catches up without re-uploading history; a
failure that belongs to one set does not stop the others.

The copy never blocks the portals. It runs only in the one-shot backup
profile, after the backup's startup lease is released, and outside any
transaction: it takes no work-order lock and holds no row lock, and each
ORM read or outcome insert commits on its own before or after the network
calls. Every request has a timeout, retries are bounded, and the whole copy,
an upload in flight included, stops at ``COPY_SECONDS``; a slow or failed
copy only records an outcome, which the pages show and the scheduler alerts
on.
"""

import fcntl
import hashlib
import logging
import os
import time
from contextlib import contextmanager, suppress

from django.db import connection

from parishkit.config import ConfigError

from .backup import MANIFEST, SET_NAME
from .backup_drive import (
    DriveClient,
    DriveFailure,
    deployment_tag,
    folder_id_from_url,
    log_timeout,
    prune,
    upload_set,
    with_retries,
    workspace_session,
)
from .observability import Event, FailureKind, emit, emit_failure
from .runtime_paths import RuntimeLayout, explicit_path, private_directory

# Sets considered per run: the newest ones not yet copied to this folder.
CATCH_UP_SETS = 3
# No new set or retry starts after this long; the backup runs twice a day.
COPY_SECONDS = 4 * 3600
SEALED_FILES = ("database.pgdump.sealed", "files.tar.sealed", MANIFEST)
# Failures that belong to one set; the copy records them and moves on.
PER_SET_FAILURES = frozenset({"verification", "unavailable"})
# One copy at a time per host: a file in the backups directory, locked for
# the whole copy and prune. Retention ignores it (it is not a directory).
COPY_LOCK = ".offsite-copy.lock"


def destination():
    """Return ``(folder_id, subject)`` from the applied configuration, or None."""
    from .accounts.runtime_models import SystemConfiguration

    runtime = (
        SystemConfiguration.objects.select_related("active_configuration")
        .only("active_configuration__canonical_document")
        .first()
    )
    if runtime is None or runtime.active_configuration is None:
        return None
    return destination_from(runtime.active_configuration.canonical_document)


def set_tag():
    """This deployment's Drive tag, from its runtime row's ID.

    Two deployments pointed at one shared-drive folder (for example a
    validation server and production) each see and prune only their own sets.
    """
    from .accounts.runtime_models import SystemConfiguration

    return deployment_tag(
        SystemConfiguration.objects.values_list("pk", flat=True).get()
    )


def destination_from(document):
    """Read the off-site destination from one canonical configuration document.

    ``None`` means off-site copies are not configured: there is no ``backup``
    integration, or no Google Workspace integration to act as. A saved link
    that no longer parses is a configuration error, not "off".
    """
    settings = {
        record["values"]["kind"]: record["values"]["settings"]
        for record in document["sections"].get("integrations", [])
    }
    if "backup" not in settings or "google_workspace" not in settings:
        return None
    try:
        folder = folder_id_from_url(settings["backup"]["target"])
    except ValueError:
        raise ConfigError("The off-site backup folder link is invalid.") from None
    return folder, settings["google_workspace"]["delegated_email"]


def _complete_sets(backups):
    """Complete local sets (those with a manifest), oldest first."""
    return sorted(
        path
        for path in backups.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and SET_NAME.match(path.name)
        and all((path / name).is_file() for name in SEALED_FILES)
    )


def _digest(directory):
    """The manifest digest the backup run recorded for this set."""
    return hashlib.sha256((directory / MANIFEST).read_bytes()).hexdigest()


def _record(state, **fields):
    """Append one outcome row; the database supplies the time."""
    from .jobs.backup_models import BackupUpload

    BackupUpload.objects.create(state=state, **fields)


def _newest_state():
    """The newest recorded outcome, or None before the first copy."""
    from .jobs.backup_models import BackupUpload

    return (
        BackupUpload.objects.order_by("-created_at")
        .values_list("state", flat=True)
        .first()
    )


def copy_offsite(
    configuration,
    *,
    session_factory=workspace_session,
    sleep=time.sleep,
    clock=time.monotonic,
):
    """Copy the newest uncopied sets and return a small JSON-able summary.

    Never raises once the destination is known: every failure is recorded
    and logged by category, and the backup command still reports the local
    set as taken.
    """
    options = {
        "session_factory": session_factory,
        "sleep": sleep,
        "clock": clock,
        "deadline": clock() + COPY_SECONDS,
    }
    target = destination()
    if target is None:
        # Record once that copies stopped on purpose, so an old failure no
        # longer alerts after the destination is removed.
        if _newest_state() not in {None, "disabled"}:
            _record("disabled")
        return {"state": "not_configured"}
    folder_id, subject = target
    try:
        return _copy_pending(configuration, folder_id, subject, **options)
    except Exception as error:
        # Anything outside the Drive categories (an unreadable local file, a
        # malformed provider reply) still records a failure, so the alert
        # fires and the pages stop showing the previous success.
        emit_failure(error, event=Event.TASK_FAILED)
        return _failed(folder_id, None, None, "unexpected")


@contextmanager
def _copy_lock(backups):
    """Hold this host's copy lock; yield False if another run holds it.

    Two overlapping runs (a manual backup started while cron's copy is still
    uploading) could otherwise replace each other's set folder mid-upload, or
    prune an older partial folder the other run is still writing (#305 L4).
    The lock is released when the file closes, even if the process dies.
    """
    descriptor = os.open(
        backups / COPY_LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def _copy_pending(configuration, folder_id, subject, **options):
    """Copy and prune under the host's copy lock.

    A run that finds the lock held leaves the copy to the run holding it and
    reports ``busy``. The holder scans for new sets once more after its
    uploads (``_copy_sets``), so a set whose own run reported ``busy`` while
    the holder was uploading is copied then, within the holder's budget,
    rather than twelve hours later by the next scheduled run.
    """
    backups = private_directory(explicit_path(configuration.paths["backups"]))
    with _copy_lock(backups) as held:
        if not held:
            return {"state": "busy"}
        return _copy_sets(backups, configuration, folder_id, subject, **options)


def _copy_sets(
    backups,
    configuration,
    folder_id,
    subject,
    *,
    session_factory,
    sleep,
    clock,
    deadline,
):
    """Upload the newest sets not yet in this folder, oldest first, then prune.

    A failure that belongs to one set (``PER_SET_FAILURES``: its copy did
    not verify, or Drive stayed unreachable through the retries) is recorded
    and the copy moves on to the next set, so one damaged or unlucky set
    cannot hold every newer one back until it leaves the catch-up window.
    Any other failure (the key, the delegation, the folder) would fail every
    set alike and stops the run. Oldest first keeps the newest set's outcome
    the last row recorded, which is what the pages and the off-site alert
    read as "the newest backup"; a failed older set is tried again next run.

    After the first batch it scans once more and copies any set that became
    pending meanwhile: a backup that finished while this run was uploading
    found the copy lock held and left its set to this run. One re-scan is
    enough, because a set completed after it takes the lock itself; the
    re-scan's uploads share the same deadline.
    """
    from .accounts.cryptography import CryptographicError
    from .accounts.key_files import read_private
    from .jobs.backup_models import BackupUpload

    def pending_sets():
        """The newest local sets with no recorded copy in this folder."""
        copied = set(
            BackupUpload.objects.filter(
                state="uploaded", folder_id=folder_id
            ).values_list("manifest_digest", flat=True)
        )
        return [
            directory
            for directory in _complete_sets(backups)[-CATCH_UP_SETS:]
            if _digest(directory) not in copied
        ]

    pending = pending_sets()
    if not pending:
        return {"state": "uploaded", "sets": 0}
    try:
        credential = read_private(
            RuntimeLayout(configuration).credential("google_workspace")
        )
        client = DriveClient(
            session_factory(credential, subject=subject),
            tag=set_tag(),
            deadline=deadline,
            budget_seconds=COPY_SECONDS,
            clock=clock,
        )
        del credential
    except (CryptographicError, ConfigError, OSError, DriveFailure):
        return _failed(folder_id, None, None, "credential")
    copied, failure, attempted = 0, None, set()
    for batch in range(2):
        if batch:
            # A set that failed in the first batch waits for the next run.
            pending = [item for item in pending_sets() if item not in attempted]
        for directory in pending:
            attempted.add(directory)
            # Each upload can take hours and the connection would sit idle
            # meanwhile; closing it first frees the slot and lets each
            # outcome insert reconnect cleanly instead of failing on a
            # dropped connection and recording nothing.
            if not connection.in_atomic_block:
                connection.close()
            digest = _digest(directory)
            if (now := clock()) >= deadline:
                # Out of time: the next run picks up the remaining sets. The
                # row says "unavailable" (its categories are fixed by the
                # schema), so the log line is what tells a stopped copy from a
                # Drive outage.
                log_timeout(
                    "drive_copy_budget",
                    limit_seconds=COPY_SECONDS,
                    elapsed_seconds=now - (deadline - COPY_SECONDS),
                )
                return _failed(folder_id, directory.name, digest, "unavailable")
            try:
                with_retries(
                    lambda directory=directory: upload_set(
                        client, folder_id, directory, SEALED_FILES
                    ),
                    sleep=sleep,
                    deadline=deadline,
                    clock=clock,
                    budget_seconds=COPY_SECONDS,
                )
            except DriveFailure as error:
                summary = _failed(folder_id, directory.name, digest, error.kind)
                # Past the budget nothing more starts; the stop is logged.
                if error.kind not in PER_SET_FAILURES or clock() >= deadline:
                    return summary
                failure = error.kind
                continue
            _record(
                "uploaded",
                set_name=directory.name,
                manifest_digest=digest,
                folder_id=folder_id,
            )
            copied += 1
    # Only sets with a recorded verified copy may be the ones Drive keeps.
    verified = set(
        BackupUpload.objects.filter(state="uploaded", folder_id=folder_id).values_list(
            "set_name", flat=True
        )
    )
    # Retention is best effort; the next successful run prunes again.
    with suppress(DriveFailure):
        prune(client, folder_id, verified=verified)
    if failure is not None:
        return {"state": "failed", "failure_kind": failure, "sets": copied}
    return {"state": "uploaded", "sets": copied}


def _failed(folder_id, set_name, digest, kind):
    """Record and log one failed copy by its Drive category; return the summary."""
    _record(
        "failed",
        set_name=set_name,
        manifest_digest=digest,
        folder_id=folder_id,
        failure_kind=kind,
    )
    emit(
        Event.TASK_FAILED,
        level=logging.WARNING,
        failure_kind=FailureKind.BACKUP_OFFSITE,
        drive_failure=kind,
    )
    return {"state": "failed", "failure_kind": kind}
