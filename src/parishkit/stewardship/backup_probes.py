"""Answer the Administrators' off-site backup "Test access" requests.

The Google Workspace credential installer runs :func:`run_pending_probes` on
each pass: it alone holds the Workspace key and network egress. Each check
writes one small file to the requested folder as the delegated user and
trashes it again, then records the outcome by fixed category. A check that
waited longer than ``PROBE_WAIT`` is closed as unanswered without contacting
Drive, matching what the page already shows, so the table keeps no stale work.
A check whose delegated user is no longer the applied one is closed as
``settings_changed``, also without contacting Drive (#269).
"""

from functools import partial

from django.db import connection, transaction

from .backup_drive import (
    PROBE_WAIT,
    DriveClient,
    DriveFailure,
    log_timeout,
    probe,
    workspace_session,
)
from .observability import Event, emit_failure

# Checks answered per installer pass. The installer publishes its health
# heartbeat only between passes, and the container is unhealthy once it is
# 90 seconds old (probe.MAX_AGE_SECONDS). A check fetches an access token and
# makes four Drive requests (folder, upload start, upload, trash), each
# waiting at most PROBE_REQUEST_SECONDS with no connection retries
# (``workspace_session``), so one check per pass stays around 75 seconds even
# when Google hangs; only name resolution is outside those timeouts. Passes
# follow every couple of seconds, so a queue of checks still drains well
# within PROBE_WAIT.
PER_PASS = 1
PROBE_REQUEST_SECONDS = 15


def _check(credential_path, row, session_factory):
    """Run one access check; return None on success or a fixed category.

    Never raises for the check itself, so one bad reply cannot abort the
    installer's pass or leave the row pending until it goes stale.
    """
    from parishkit.config import ConfigError

    from .accounts.cryptography import CryptographicError
    from .accounts.key_files import read_private

    try:
        credential = read_private(credential_path)
    except (CryptographicError, ConfigError, OSError):
        return "credential"
    try:
        client = DriveClient(
            session_factory(credential, subject=row.subject),
            request_seconds=PROBE_REQUEST_SECONDS,
        )
        del credential
        probe(client, row.folder_id)
    except DriveFailure as failure:
        return failure.kind
    except Exception as error:
        emit_failure(error, event=Event.TASK_FAILED)
        return "unexpected"
    return None


def applied_mailbox():
    """The applied delegated mailbox user, or None when none is applied.

    The installer cannot read the applied settings; the definer
    ``stewardship_backup_destination_v1()`` (migration 0176) answers it.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT delegated_email FROM stewardship_backup_destination_v1()"
        )
        return cursor.fetchone()[0]


def has_pending():
    """Whether any access check is waiting: one cheap read for the idle loop (#639)."""
    from .jobs.backup_models import BackupDriveProbe

    return BackupDriveProbe.objects.filter(state="pending").exists()


def run_pending_probes(
    credential_path, *, profile, redirect=None, session_factory=None, check=lambda: None
):
    """Complete up to ``PER_PASS`` pending checks; return how many finished.

    ``profile`` and ``redirect`` are the installer's deployment profile and
    mail redirect; the default session is ``workspace_session`` bound to
    them, so a LOCAL check without a redirect (or under the fake clock)
    fails as a credential problem without contacting Drive (#1399).

    Drive is contacted outside any transaction: a slow reply never holds a
    database transaction or row lock open. ``check`` (the installer's lease
    check) runs before each one. This installer is the only process that
    completes checks, and the SQL guard lets a check complete only once.

    The insert guard admits only the applied delegated mailbox user, but an
    Administrator may change that user (for example away from a compromised
    account) while a check waits. So each check is compared with the applied
    user right before it runs, and one queued for another user is closed as
    ``settings_changed`` without impersonating anyone (#269). The folder is
    not compared: Test access checks the folder the Administrator entered,
    usually before it is saved, and runs only as the applied user anyway.
    The comparison is made just before impersonation, not under a lock held
    through the Drive calls (which would hold a transaction open for up to a
    minute), so a change committed in the milliseconds between the read and
    the session being built still lets that one check run as the old user.
    """
    from .jobs.backup_models import BackupDriveProbe
    from .jobs.ownership import database_now

    if session_factory is None:
        session_factory = partial(workspace_session, profile=profile, redirect=redirect)
    finished = 0
    for _ in range(PER_PASS):
        check()
        with transaction.atomic():
            row = (
                BackupDriveProbe.objects.filter(state="pending")
                .order_by("created_at")
                .first()
            )
            if row is None:
                return finished
            waited = database_now() - row.created_at
        if waited > PROBE_WAIT:
            # Closed unanswered at its limit: say what waited and for how long.
            log_timeout(
                "drive_probe_wait",
                limit_seconds=PROBE_WAIT.total_seconds(),
                elapsed_seconds=waited.total_seconds(),
            )
            kind = "unanswered"
        elif row.subject != applied_mailbox():
            kind = "settings_changed"
        else:
            kind = _check(credential_path, row, session_factory)
        with transaction.atomic():
            BackupDriveProbe.objects.filter(pk=row.pk, state="pending").update(
                state="failed" if kind else "succeeded", failure_kind=kind
            )
        finished += 1
    return finished
