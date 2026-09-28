"""One append-only row per completed backup: the evidence upgrades and alerts read."""

import uuid

from django.db import models
from django.db.models.functions import Now

from parishkit.stewardship.storage import UTCDateTimeField


class BackupRun(models.Model):
    """A completed, sealed backup set: sizes, digests and the key it is for.

    Only successes are recorded; a failed run leaves no row, so the overdue
    check reads absence. Nothing here names a path, a host or a credential.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    started_at = UTCDateTimeField()
    completed_at = UTCDateTimeField(db_default=Now())
    database_bytes = models.BigIntegerField()
    files_bytes = models.BigIntegerField()
    manifest_digest = models.CharField(max_length=64)
    recipient_fingerprint = models.CharField(max_length=16)
    application_version = models.CharField(max_length=40)

    class Meta:
        db_table = "stewardship_backup_run"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(started_at__lte=models.F("completed_at")),
                name="backup_run_times",
            ),
            models.CheckConstraint(
                condition=models.Q(database_bytes__gte=0, files_bytes__gte=0),
                name="backup_run_sizes",
            ),
            models.CheckConstraint(
                condition=models.Q(manifest_digest__regex=r"^[0-9a-f]{64}$")
                & models.Q(recipient_fingerprint__regex=r"^[0-9a-f]{16}$"),
                name="backup_run_digests",
            ),
        ]


FAILURE_KINDS = (
    "authorization",
    "api_disabled",
    "not_found",
    "permission",
    "not_folder",
    "credential",
    "verification",
    "unavailable",
    "unexpected",
    "unanswered",
)
# Drive folder IDs; the same pattern backup_drive.FOLDER_ID accepts.
FOLDER_PATTERN = r"^[A-Za-z0-9_-]{10,200}$"


class BackupUpload(models.Model):
    """The outcome of copying one completed set to the off-site Drive folder.

    Append-only like :class:`BackupRun`. ``disabled`` records that the
    destination was removed, so an old failure stops alerting. Names only a
    Drive folder ID and the set's name and manifest digest.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = UTCDateTimeField(db_default=Now())
    state = models.CharField(max_length=16)
    set_name = models.CharField(max_length=16, null=True)
    manifest_digest = models.CharField(max_length=64, null=True)
    folder_id = models.CharField(max_length=200, null=True)
    failure_kind = models.CharField(max_length=32, null=True)

    class Meta:
        db_table = "stewardship_backup_upload"
        indexes = [
            models.Index(models.F("created_at").desc(), name="backup_upload_newest")
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=["uploaded", "failed", "disabled"]),
                name="backup_upload_state",
            ),
            models.CheckConstraint(
                condition=models.Q(failure_kind__isnull=True)
                | models.Q(failure_kind__in=FAILURE_KINDS),
                name="backup_upload_failure_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state="uploaded",
                    set_name__isnull=False,
                    manifest_digest__isnull=False,
                    folder_id__isnull=False,
                    failure_kind__isnull=True,
                )
                | models.Q(
                    state="failed",
                    folder_id__isnull=False,
                    failure_kind__isnull=False,
                )
                | models.Q(
                    state="disabled",
                    set_name__isnull=True,
                    manifest_digest__isnull=True,
                    folder_id__isnull=True,
                    failure_kind__isnull=True,
                ),
                name="backup_upload_shape",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(set_name__isnull=True)
                    | models.Q(set_name__regex=r"^[0-9]{8}T[0-9]{6}Z$")
                )
                & (
                    models.Q(manifest_digest__isnull=True)
                    | models.Q(manifest_digest__regex=r"^[0-9a-f]{64}$")
                )
                & (
                    models.Q(folder_id__isnull=True)
                    | models.Q(folder_id__regex=FOLDER_PATTERN)
                ),
                name="backup_upload_values",
            ),
        ]


class BackupDriveProbe(models.Model):
    """An Administrator's one-time check that the Drive folder accepts backups."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = UTCDateTimeField(db_default=Now())
    requested_by_id = models.UUIDField()
    folder_id = models.CharField(max_length=200)
    subject = models.CharField(max_length=254)
    state = models.CharField(max_length=16, db_default="pending")
    failure_kind = models.CharField(max_length=32, null=True)
    completed_at = UTCDateTimeField(null=True)

    class Meta:
        db_table = "stewardship_backup_drive_probe"
        indexes = [
            models.Index(
                fields=["created_at"],
                name="backup_probe_pending",
                condition=models.Q(state="pending"),
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=["pending", "succeeded", "failed"]),
                name="backup_probe_state",
            ),
            models.CheckConstraint(
                condition=models.Q(folder_id__regex=FOLDER_PATTERN),
                name="backup_probe_folder",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state="pending",
                    failure_kind__isnull=True,
                    completed_at__isnull=True,
                )
                | models.Q(
                    state="succeeded",
                    failure_kind__isnull=True,
                    completed_at__isnull=False,
                )
                | models.Q(
                    state="failed",
                    failure_kind__isnull=False,
                    completed_at__isnull=False,
                ),
                name="backup_probe_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(failure_kind__isnull=True)
                | models.Q(failure_kind__in=FAILURE_KINDS),
                name="backup_probe_failure_kind",
            ),
        ]
