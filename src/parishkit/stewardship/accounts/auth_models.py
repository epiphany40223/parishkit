"""Safe authentication incidents and durable notification intent, never counters."""

from django.db import models

from parishkit.stewardship.storage import MutableRecord, UTCDateTimeField


class OAuthStateConsumption(models.Model):
    """Opaque one-use receipts prevent concurrent replay of session-stored state."""

    fingerprint = models.CharField(max_length=64, primary_key=True)
    expires_at = UTCDateTimeField(db_index=True)

    class Meta:
        db_table = "stewardship_oauth_consumption"


class AuthenticationIncident(MutableRecord):
    """Deduplicated operations work survives loss of the ephemeral limiter store."""

    kind = models.CharField(max_length=32)
    window = models.PositiveBigIntegerField()
    level = models.CharField(max_length=16)
    attempts = models.PositiveIntegerField()
    sources = models.PositiveIntegerField()
    identities = models.PositiveIntegerField()
    candidates = models.PositiveIntegerField()
    resolved_at = UTCDateTimeField(null=True)
    notification_pending = models.BooleanField(default=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_auth_incident"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["kind", "window"], name="auth_incident_window"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=[
                        "limiter_unavailable",
                        "limiter_state_lost",
                        "admin_abuse",
                        "family_abuse",
                    ]
                ),
                name="auth_incident_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(level__in=["WARNING", "CRITICAL"]),
                name="auth_incident_level",
            ),
            models.UniqueConstraint(
                fields=["kind"],
                condition=models.Q(
                    kind="limiter_unavailable", resolved_at__isnull=True
                ),
                name="auth_single_limiter_outage",
            ),
        ]


class LimiterStoreHealth(MutableRecord):
    """Durable non-identifying baseline detects loss across application restarts."""

    namespace_fingerprint = models.CharField(max_length=64, unique=True)
    run_id = models.CharField(max_length=40)
    marker = models.UUIDField()
    evicted_keys = models.PositiveBigIntegerField()

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_limiter_health"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(namespace_fingerprint__regex=r"^[0-9a-f]{64}$"),
                name="limiter_health_namespace",
            ),
            models.CheckConstraint(
                condition=models.Q(run_id__regex=r"^[0-9a-f]{40}$"),
                name="limiter_health_run_id",
            ),
        ]
