"""Credential receipts, separately sealed staging, and authenticated consumer evidence.

Permanent receipts never contain secret bytes or paths. The separate expiring
staging table holds only target-key ciphertext and is scrubbed after installation
or before a failed/cancelled terminal checkpoint. SQL identity and row policies
restrict installers and consumers; models themselves do not grant authority.
"""

from datetime import timedelta

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
SECRET_STATES = (
    "staged",
    "testing",
    "installing",
    "awaiting_ack",
    "cleanup_pending",
    "cancelled",
    "expired",
    "failed",
    "applied",
)
SECRET_PENDING = ("staged", "testing", "installing", "awaiting_ack", "cleanup_pending")
MAX_STAGING_LIFETIME = timedelta(hours=24)


class SecretReplacementRequest(MutableRecord):
    """Reserve a target until installation or cleanup has durably completed.

    Request UUID and payload reference are never reused, including after cleanup.
    Actor/reauthentication fields are evidence supplied by authenticated admission,
    not proof of current authorization. Installer and consumer database identities
    separately own each transition and its append-only checkpoint.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "target",
        "staging_reference",
        "requested_by_id",
        "reauthenticated_at",
        "expires_at",
        "expected_fingerprint",
        "required_consumers",
    )
    write_once_fields = (
        "resulting_fingerprint",
        "installed_at",
        "acknowledged_at",
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
    required_consumers = models.JSONField(default=list)
    resulting_fingerprint = models.CharField(max_length=64, null=True, blank=True)
    installed_at = UTCDateTimeField(null=True, blank=True)
    acknowledged_at = UTCDateTimeField(null=True, blank=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_secret_request"
        indexes = [models.Index(fields=["state", "expires_at"], name="secret_expiry")]
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["target"],
                condition=models.Q(state__in=SECRET_PENDING),
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
                condition=models.Q(
                    expires_at__lte=models.F("created_at") + MAX_STAGING_LIFETIME
                ),
                name="secret_max_staging_lifetime",
            ),
            models.CheckConstraint(
                condition=models.Q(expected_fingerprint__isnull=True)
                | models.Q(expected_fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="secret_safe_fingerprint",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=["staged", "testing", "installing", "awaiting_ack"],
                    cleanup_reason="",
                    scrubbed_at__isnull=True,
                )
                | models.Q(
                    state="cleanup_pending",
                    cleanup_reason__in=["cancelled", "expired", "failed", "applied"],
                    scrubbed_at__isnull=True,
                )
                | (
                    models.Q(
                        state__in=["cancelled", "expired", "failed", "applied"],
                        cleanup_reason=models.F("state"),
                        scrubbed_at__isnull=False,
                    )
                    & models.Q(scrubbed_at__gte=models.F("created_at"))
                ),
                name="secret_cleanup_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(resulting_fingerprint__isnull=True)
                | models.Q(resulting_fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="secret_result_fingerprint",
            ),
            models.CheckConstraint(
                condition=~models.Q(state__in=["installing", "awaiting_ack", "applied"])
                | models.Q(resulting_fingerprint__isnull=False),
                name="secret_install_has_fingerprint",
            ),
            models.CheckConstraint(
                condition=~models.Q(state__in=["awaiting_ack", "applied"])
                | models.Q(installed_at__isnull=False),
                name="secret_installed_has_instant",
            ),
            models.CheckConstraint(
                condition=~models.Q(state="applied")
                | models.Q(acknowledged_at__isnull=False),
                name="secret_applied_has_ack",
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


class SealedCredentialStaging(models.Model):
    """Expiring target-owned ciphertext store, separate from permanent receipts.

    The web service can insert newly sealed candidates, never read them back.
    Target identity and request bindings are enforced by SQL and row policies.
    Payload bytes are scrubbed before the request's terminal checkpoint commits.
    """

    reference = models.UUIDField(primary_key=True)
    request = models.OneToOneField(SecretReplacementRequest, on_delete=models.PROTECT)
    target = models.CharField(max_length=32)
    ciphertext = models.TextField(null=True)
    fingerprint = models.CharField(max_length=64)

    class Meta:
        db_table = "stewardship_sealed_credential_staging"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="sealed_staging_fingerprint",
            ),
            models.CheckConstraint(
                condition=models.Q(target__in=SECRET_TARGETS),
                name="sealed_staging_target",
            ),
        ]


class CredentialConsumerAcknowledgement(ImmutableRecord):
    """A consumer database identity attests the fingerprint it actually loaded."""

    request = models.ForeignKey(SecretReplacementRequest, on_delete=models.PROTECT)
    consumer = models.CharField(max_length=32)
    fingerprint = models.CharField(max_length=64)

    class Meta:
        db_table = "stewardship_credential_consumer_ack"
        constraints = [
            models.UniqueConstraint(
                fields=["request", "consumer"], name="credential_ack_consumer_once"
            ),
            models.CheckConstraint(
                condition=models.Q(fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="credential_ack_fingerprint",
            ),
        ]
