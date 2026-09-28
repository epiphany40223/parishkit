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

# A finished check (passed, failed or unanswered) is shown on the page for at
# most this long after it finished, and not at all once settings have been
# applied after it; a pending one shows until it finishes or PROBE_WAIT ends.
PROBE_SHOWN_FOR = timedelta(minutes=10)


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
    # The link of the folder that was tested, so the page can show it and
    # keep it in the folder field for Save, and when the test was requested.
    folder_url: str = ""
    requested_at: datetime | None = None
    folder_id: str = ""
    # Whether the saved backup folder changed after this check was requested
    # (the Administrator saved a folder since testing).
    saved_after: bool = False


def folder_link(folder_id):
    """The canonical Google Drive link for one folder ID."""
    return f"https://drive.google.com/drive/folders/{folder_id}"


def latest_probe(actor_id, now):
    """The actor's newest access check while it is still worth showing, or None.

    Only the newest check counts: a new "Test access" replaces the previous
    result, and an older one never shows in its place. A pending check shows
    until it finishes or ``PROBE_WAIT`` ends. A finished one (passed, failed,
    or unanswered) shows for ``PROBE_SHOWN_FOR`` after it finished, and
    disappears once the tested folder has been saved as the backup folder
    after the test. Other configuration changes don't hide it.
    """
    row = (
        BackupDriveProbe.objects.filter(
            requested_by_id=actor_id,
            created_at__gte=now - PROBE_WAIT - PROBE_SHOWN_FOR,
        )
        .order_by("-created_at")
        .first()
    )
    if row is None:
        return None
    current, saved_after = saved_folder_since(row.created_at)
    if not _still_shown(row, now, saved_after and current == row.folder_id):
        return None
    folder_url = folder_link(row.folder_id)
    if row.state == "succeeded":
        return ProbeStatus(
            "succeeded",
            _(
                "Access confirmed: a test file was written to the folder and "
                "removed again."
            ),
            row.completed_at,
            folder_url,
            row.created_at,
            row.folder_id,
            saved_after,
        )
    # The installer never runs a check older than PROBE_WAIT, so a pending
    # one past it is final in all but its recorded close.
    if row.failure_kind == "unanswered" or (
        row.state == "pending" and now - row.created_at > PROBE_WAIT
    ):
        return ProbeStatus(
            "unanswered",
            DriveFailure("unanswered").message,
            row.created_at,
            folder_url,
            row.created_at,
            row.folder_id,
            saved_after,
        )
    if row.state == "failed":
        return ProbeStatus(
            "failed",
            DriveFailure(row.failure_kind).message,
            row.completed_at,
            folder_url,
            row.created_at,
            row.folder_id,
            saved_after,
        )
    return ProbeStatus(
        "pending",
        _("Checking access to the folder…"),
        row.created_at,
        folder_url,
        row.created_at,
        row.folder_id,
        saved_after,
    )


def _still_shown(row, now, saved):
    """Whether one check's result still belongs on the page (see latest_probe).

    ``saved`` means the tested folder was saved as the backup folder after
    the test was requested.
    """
    expired = now - row.created_at > PROBE_WAIT
    if row.state == "pending" and not expired:
        return True
    if saved:
        return False
    # An unanswered check, whether the installer closed it or not, ended when
    # PROBE_WAIT ran out; the others ended when they completed.
    if row.state == "pending" or row.failure_kind == "unanswered":
        finished = row.created_at + PROBE_WAIT
    else:
        finished = row.completed_at
    return now - finished <= PROBE_SHOWN_FOR


def _backup_folder(document):
    """The backup folder ID saved in one configuration document, or None."""
    from parishkit.stewardship.backup_drive import folder_id_from_url

    for record in document["sections"].get("integrations", []):
        if record["values"]["kind"] == "backup":
            try:
                return folder_id_from_url(record["values"]["settings"]["target"])
            except (ValueError, KeyError, TypeError):
                return None
    return None


def saved_folder_since(since):
    """Return ``(current_folder_id, changed)`` for the saved backup folder.

    ``changed`` says whether any configuration applied after ``since``
    changed the backup folder. It walks back only through the versions
    applied since then (a few minutes' worth), comparing each with its
    predecessor, so unrelated configuration changes don't count.
    """
    from .configuration_models import AppliedConfigurationVersion
    from .runtime_models import SystemConfiguration

    active = SystemConfiguration.objects.values_list(
        "active_configuration_id", flat=True
    ).first()
    version = (
        AppliedConfigurationVersion.objects.filter(pk=active)
        .only("created_at", "canonical_document", "predecessor_id")
        .first()
        if active
        else None
    )
    current = _backup_folder(version.canonical_document) if version else None
    folder = current
    while version is not None and version.created_at > since:
        previous = (
            AppliedConfigurationVersion.objects.filter(pk=version.predecessor_id)
            .only("created_at", "canonical_document", "predecessor_id")
            .first()
        )
        earlier = _backup_folder(previous.canonical_document) if previous else None
        if earlier != folder:
            return current, True
        version, folder = previous, earlier
    return current, False


def request_probe(actor_id, folder_id, subject):
    """Queue one access check; the Workspace installer completes it."""
    return BackupDriveProbe.objects.create(
        requested_by_id=actor_id, folder_id=folder_id, subject=subject
    )
