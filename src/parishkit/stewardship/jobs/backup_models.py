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
