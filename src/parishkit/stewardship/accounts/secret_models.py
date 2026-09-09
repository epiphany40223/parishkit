"""Non-secret credential handoff metadata; no installer authority is granted here.

The staged payload lives in a target-owned external store. These records never
accept its bytes, a file path, arbitrary errors, or a credential value. A later
ARC-06 service supplies encryption, authenticated target identity and installation.
"""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)

# Freeze this storage vocabulary independently of deployment parser changes.
SECRET_TARGETS = (
    "django_signing",
    "general_encryption",
    "family_code_mac",
    "token_public",
    "token_private",
    "google_oauth",
    "google_workspace",
    "parishsoft",
    "slack",
    "backup_target",
    "backup_data",
    "metrics",
)
SECRET_STATES = ("staged", "cleanup_pending", "cancelled", "expired")


class SecretReplacementRequest(MutableRecord):
    """Reserve a target until cancellation/expiry has durably scrubbed staging.

    Request UUID and payload reference are never reused, including after cleanup.
    Actor/reauthentication fields are evidence supplied by a trusted future admission
    service, not proof of current authorization. No installation state is admitted.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "target",
        "staging_reference",
        "requested_by_id",
        "reauthenticated_at",
        "expires_at",
        "expected_fingerprint",
    )
    target = models.CharField(max_length=32)
    staging_reference = models.UUIDField(unique=True)
    requested_by_id = models.UUIDField()
    reauthenticated_at = UTCDateTimeField()
    expires_at = UTCDateTimeField()
    expected_fingerprint = models.CharField(max_length=64, null=True, blank=True)
    state = models.CharField(max_length=16, default="staged", db_default="staged")
    cleanup_reason = models.CharField(max_length=16, default="", db_default="")
    scrubbed_at = UTCDateTimeField(null=True, blank=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_secret_request"
        indexes = [models.Index(fields=["state", "expires_at"], name="secret_expiry")]
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["target"],
                condition=models.Q(state__in=["staged", "cleanup_pending"]),
                name="secret_one_pending_target",
            ),
            models.CheckConstraint(
                condition=models.Q(target__in=SECRET_TARGETS),
                name="secret_known_target",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=SECRET_STATES), name="secret_known_state"
            ),
            models.CheckConstraint(
                condition=models.Q(reauthenticated_at__lte=models.F("created_at"))
                & models.Q(expires_at__gt=models.F("created_at")),
                name="secret_valid_interval",
            ),
            models.CheckConstraint(
                condition=models.Q(expected_fingerprint__isnull=True)
                | models.Q(expected_fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="secret_safe_fingerprint",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state="staged", cleanup_reason="", scrubbed_at__isnull=True
                )
                | models.Q(
                    state="cleanup_pending",
                    cleanup_reason__in=["cancelled", "expired"],
                    scrubbed_at__isnull=True,
                )
                | (
                    models.Q(
                        state__in=["cancelled", "expired"],
                        cleanup_reason=models.F("state"),
                        scrubbed_at__isnull=False,
                    )
                    & models.Q(scrubbed_at__gte=models.F("created_at"))
                ),
                name="secret_cleanup_shape",
            ),
        ]


class SecretRequestCheckpoint(ImmutableRecord):
    """Append-only transition evidence, atomically paired with request and audit."""

    request = models.ForeignKey(
        SecretReplacementRequest, on_delete=models.PROTECT, related_name="checkpoints"
    )
    sequence = models.PositiveBigIntegerField()
    state = models.CharField(max_length=16)

    class Meta:
        db_table = "stewardship_secret_checkpoint"
        constraints = [
            models.UniqueConstraint(
                fields=["request", "sequence"], name="secret_checkpoint_sequence"
            ),
            models.CheckConstraint(
                condition=models.Q(sequence__gte=1), name="secret_checkpoint_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=SECRET_STATES),
                name="secret_checkpoint_state",
            ),
        ]
