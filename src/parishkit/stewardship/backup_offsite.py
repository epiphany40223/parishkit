"""The off-site copy of sealed backup sets to the configured Google Drive folder.

Since the contract release (#235) only the dedicated off-site uploader
(``uploader_commands``) copies: the backup profile, which reads every tree
and all database data, has no route out and never copies. What lives here is
the copy itself, shared by the uploader's ``upload`` and ``smoke --send``:
the host's copy lock, the per-set uploads with their budgets, retries and
deadline, the outcome rows and Drive retention. The destination is the
applied configuration's ``backup`` integration (a Drive folder link); the
identity is the Google Workspace integration's delegated mailbox user, and
the key is the installed Workspace credential, which the uploader mounts by
itself. A failed copy leaves the local set and its record in place, appends a
``failed`` outcome the scheduler alerts on, and the next pass tries again.

Only the newest few sets not yet copied to the current folder are attempted,
oldest first, so a long outage catches up without re-uploading history; a
failure that belongs to one set does not stop the others.

The copy never blocks the portals. It runs only in the one-shot uploader
profile, after its startup lease is released, and outside any transaction:
it takes no work-order lock and holds no row lock, and each ORM read or
outcome insert commits on its own before or after the network calls. Every
request has a timeout, retries are bounded, and the whole copy, an upload in
flight included, stops at ``COPY_SECONDS``, or at most
``backup_drive.UPLOAD_SECONDS`` later when a socket operation stalls just
then; a slow or failed copy only records an outcome, which the pages show
and the scheduler alerts on.
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
    MANIFEST_CAP,
    DriveClient,
    DriveFailure,
    deployment_tag,
    folder_id_from_url,
    log_timeout,
    prune,
    single_regular,
    upload_set,
    with_retries,
)
from .observability import Event, FailureKind, emit

# Sets considered per run: the newest ones not yet copied to this folder.
CATCH_UP_SETS = 3
# No new set or retry starts after this long; the backup runs twice a day.
COPY_SECONDS = 4 * 3600
SEALED_FILES = ("database.pgdump.sealed", "files.tar.sealed", MANIFEST)
# Failures that belong to one set; the copy records them and moves on.
PER_SET_FAILURES = frozenset({"verification", "unavailable"})
# One copy at a time per host, locked for the whole copy and prune: an
# exclusive flock on the backups directory itself (#235), which the uploader
# takes through its read-only mount and the backup's retention takes without
# waiting (``backup.copy_lock_free``). The expand release's legacy
# ``.offsite-copy.lock`` file lock is gone since the contract release; a
# leftover file is harmless, as retention looks only at directories.
# How often a copier waiting for the copy lock (``--wait-lock``) tries again.
LOCK_POLL_SECONDS = 1


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
    """The manifest digest the backup run recorded for this set, or None.

    The manifest is read as ``backup_drive.open_set`` reads it (#235): the
    set's directory and the file are opened without following a link, the
    file must be a regular one with a single link, and nothing past
    ``MANIFEST_CAP`` is read.
    None means the manifest broke one of those rules (or could not be read):
    the uploader then skips the set silently (``uploader_pending``).
    Nothing is logged here, so choosing what to copy stays silent.
    """
    try:
        folder = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            descriptor = os.open(
                MANIFEST, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder
            )
        finally:
            os.close(folder)
        with os.fdopen(descriptor, "rb") as handle:
            if not single_regular(os.fstat(descriptor)):
                return None
            data = handle.read(MANIFEST_CAP + 1)
    except OSError:
        return None
    if len(data) > MANIFEST_CAP:
        return None
    return hashlib.sha256(data).hexdigest()


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


class CopyLockTimeout(Exception):
    """A copier's bounded wait for the copy lock (``--wait-lock``) expired."""

    def __init__(self, limit_seconds, elapsed_seconds):
        super().__init__("lock_timeout")
        self.limit_seconds = limit_seconds
        self.elapsed_seconds = elapsed_seconds


@contextmanager
def _copy_lock(backups, *, wait_seconds=None, clock=time.monotonic, sleep=time.sleep):
    """Hold this host's copy lock; yield False if another run holds it.

    Two overlapping copies (an operator's ``smoke --send`` started while the
    cron uploader is still uploading) could otherwise replace each other's
    set folder mid-upload, or prune an older partial folder the other is
    still writing (#305 L4); retention must not remove a set mid-upload
    either. The lock is an exclusive flock on the backups directory, opened
    ``O_RDONLY|O_DIRECTORY|O_NOFOLLOW`` (#235), which works through the
    uploader's read-only mount and across containers, since they share the
    host inode. It is released when the descriptor closes, even if the
    process dies. With ``wait_seconds`` the lock is waited for, at most
    that long, and an expired wait raises ``CopyLockTimeout`` for the caller
    to log; without it a held lock yields False at once.
    """
    descriptor = os.open(backups, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        started = clock()
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if wait_seconds is None:
                    yield False
                    return
                if (elapsed := clock() - started) >= wait_seconds:
                    raise CopyLockTimeout(wait_seconds, elapsed) from None
                sleep(LOCK_POLL_SECONDS)
        yield True
    finally:
        os.close(descriptor)


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
    pending_sets,
    key,
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
    would otherwise wait for a cron pass after this one, which the held copy
    lock turns away until this run ends. One re-scan is enough; its uploads
    share the same deadline.

    ``pending_sets()`` returns ``(directory, digest, refusal)`` triples (the
    uploader's ``uploader_pending``), whose refusal (``verification``) is
    recorded for a set that must not be copied. ``key`` is the Workspace
    key's path, which the uploader mounts by itself. ``configuration`` is
    the uploader's own.
    """
    from .accounts.cryptography import CryptographicError
    from .accounts.key_files import read_private
    from .jobs.backup_models import BackupUpload

    pending = pending_sets()
    if not pending:
        return {"state": "uploaded", "sets": 0}
    try:
        credential = read_private(key)
        session = session_factory(credential, subject=subject)
        del credential
        client = DriveClient(
            session,
            tag=set_tag(),
            deadline=deadline,
            budget_seconds=COPY_SECONDS,
            clock=clock,
        )
    except (CryptographicError, ConfigError, OSError, DriveFailure):
        return _failed(folder_id, None, None, "credential")
    copied, failure, attempted = 0, None, set()
    for batch in range(2):
        if batch:
            # A set that failed in the first batch waits for the next run.
            pending = [item for item in pending_sets() if item[0] not in attempted]
        for directory, digest, refusal in pending:
            attempted.add(directory)
            # Each upload can take hours and the connection would sit idle
            # meanwhile; closing it first frees the slot and lets each
            # outcome insert reconnect cleanly instead of failing on a
            # dropped connection and recording nothing.
            if not connection.in_atomic_block:
                connection.close()
            if refusal is not None:
                # The set itself is wrong (#235, "Handoff"): recorded and
                # logged like a mismatched set, and the copy moves on.
                _failed(folder_id, directory.name, digest, refusal)
                failure = refusal
                continue
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
                    # The digest this set was chosen (and, for the
                    # uploader, bound) by: a set swapped on disk since is
                    # refused, not uploaded under the row's digest (#235).
                    lambda directory=directory, digest=digest: upload_set(
                        client,
                        folder_id,
                        directory,
                        SEALED_FILES,
                        expected_digest=digest,
                    ),
                    sleep=sleep,
                    deadline=deadline,
                    clock=clock,
                    budget_seconds=COPY_SECONDS,
                )
            except DriveFailure as error:
                summary = _failed(folder_id, directory.name, digest, error.kind)
                # Once the budget stopped a request, an upload or a retry,
                # nothing more starts; the stop is already logged.
                if error.kind not in PER_SET_FAILURES or error.budget_stop:
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
    # Retention is best effort; the next successful run prunes again. It is a
    # few small listings and trashes, each bounded by its own request
    # timeout, and runs outside the copy budget: a budget running out during
    # prune would log a stopped copy after a run that copied every set.
    with suppress(DriveFailure):
        prune(DriveClient(session, tag=client.tag), folder_id, verified=verified)
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
