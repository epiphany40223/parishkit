"""Web-side view of off-site backups: status lines and "Test access" requests.

The web process holds no Google key and no network egress, so it never talks
to Drive. It reads the outcomes the backup profile records, and queues access
checks that the Google Workspace credential installer completes.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.backup_drive import PROBE_WAIT, DriveFailure
from parishkit.stewardship.jobs.backup_models import BackupDriveProbe, BackupUpload

# An Admin's own recent check is shown on the page for this long.
PROBE_SHOWN_FOR = timedelta(hours=1)


@dataclass(frozen=True)
class OffsiteStatus:
    """The newest off-site copy outcome, in plain language."""

    kind: str  # "uploaded", "failed", "disabled" or "none"
    message: str
    at: datetime | None
    last_copy_at: datetime | None
    set_name: str | None


def offsite_status():
    """Describe the newest recorded copy and the newest successful one."""
    newest = BackupUpload.objects.order_by("-created_at").first()
    # The newest row usually is the last success; look further only if not.
    copied = (
        newest
        if newest is not None and newest.state == "uploaded"
        else BackupUpload.objects.filter(state="uploaded")
        .order_by("-created_at")
        .first()
    )
    last_copy_at = copied.created_at if copied else None
    set_name = copied.set_name if copied else None
    if newest is None:
        return OffsiteStatus(
            "none",
            _("No backup has been copied off-site yet."),
            None,
            None,
            None,
        )
    if newest.state == "uploaded":
        return OffsiteStatus(
            "uploaded",
            _("The newest backup was copied to Google Drive."),
            newest.created_at,
            last_copy_at,
            set_name,
        )
    if newest.state == "disabled":
        return OffsiteStatus(
            "disabled",
            _("Off-site copies are turned off."),
            newest.created_at,
            last_copy_at,
            set_name,
        )
    return OffsiteStatus(
        "failed",
        DriveFailure(newest.failure_kind).message,
        newest.created_at,
        last_copy_at,
        set_name,
    )


@dataclass(frozen=True)
class ProbeStatus:
    """One Administrator's latest "Test access" result."""

    kind: str  # "pending", "succeeded", "failed" or "unanswered"
    message: str
    at: datetime


def latest_probe(actor_id, now):
    """The actor's newest access check within the last hour, or None."""
    row = (
        BackupDriveProbe.objects.filter(
            requested_by_id=actor_id, created_at__gte=now - PROBE_SHOWN_FOR
        )
        .order_by("-created_at")
        .first()
    )
    if row is None:
        return None
    if row.state == "succeeded":
        return ProbeStatus(
            "succeeded",
            _(
                "Access confirmed: a test file was written to the folder and "
                "removed again."
            ),
            row.completed_at,
        )
    # The installer never runs a check older than PROBE_WAIT, so a pending
    # one past it is final in all but its recorded close.
    if row.failure_kind == "unanswered" or (
        row.state == "pending" and now - row.created_at > PROBE_WAIT
    ):
        return ProbeStatus(
            "unanswered", DriveFailure("unanswered").message, row.created_at
        )
    if row.state == "failed":
        return ProbeStatus(
            "failed", DriveFailure(row.failure_kind).message, row.completed_at
        )
    return ProbeStatus("pending", _("Checking access to the folder…"), row.created_at)


def request_probe(actor_id, folder_id, subject):
    """Queue one access check; the Workspace installer completes it."""
    return BackupDriveProbe.objects.create(
        requested_by_id=actor_id, folder_id=folder_id, subject=subject
    )
