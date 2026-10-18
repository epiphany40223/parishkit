"""Wizard-only sealed inputs; never consumed by the live replacement queue."""

from django.db import models

from parishkit.stewardship.storage import MutableRecord, UTCDateTimeField


class SetupSealedCredential(MutableRecord):
    """One replaceable collecting-stage input per target and original attempt.

    SQL column grants deny web/scheduler ciphertext reads. Only the matching
    target installer may decrypt a candidate. Loading freezes replacement;
    terminal setup scrubs ciphertext atomically without erasing its receipt,
    and so does turning Slack off while collecting, for the Slack token
    (#1499). A scrubbed row is never repopulated: a new token is a new row.
    """

    immutable_fields = MutableRecord.immutable_fields + ("attempt_id", "target")
    write_once_fields = ("scrubbed_at",)
    attempt = models.ForeignKey("SetupAttempt", on_delete=models.PROTECT)
    target = models.CharField(max_length=32)
    ciphertext = models.TextField(null=True)
    fingerprint = models.CharField(max_length=64)
    settings = models.JSONField()
    scrubbed_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_setup_sealed_credential"
        constraints = MutableRecord.Meta.constraints + [
            # One live (unscrubbed) row per target: a Slack token discarded
            # by turning Slack off stays as the audit's tombstone beside a
            # newly staged one (#1499).
            models.UniqueConstraint(
                fields=["attempt", "target"],
                condition=models.Q(scrubbed_at=None),
                name="setup_secret_one_live_target",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    target__in=["parishsoft", "google_workspace", "slack"]
                ),
                name="setup_secret_target",
            ),
            models.CheckConstraint(
                condition=models.Q(fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="setup_secret_fingerprint",
            ),
            models.CheckConstraint(
                condition=models.Q(ciphertext__isnull=False, scrubbed_at__isnull=True)
                | models.Q(ciphertext__isnull=True, scrubbed_at__isnull=False),
                name="setup_secret_scrub_shape",
            ),
        ]
