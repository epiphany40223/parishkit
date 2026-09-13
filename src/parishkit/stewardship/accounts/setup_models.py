"""Initial-setup attempt ownership; staging is never applied configuration.

Only opaque session/task identifiers and closed lifecycle metadata are retained
here. Session UUIDs deliberately are not foreign keys: expired login records can
be cleaned while setup's safe tombstone and audit references remain durable.
"""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)

from .setup_content_values import CONTENT_STEPS


class SetupAttempt(MutableRecord):
    """One deployment-wide attempt, permanently bound to one original Admin login."""

    immutable_fields = MutableRecord.immutable_fields + (
        "session_id",
        "owner_id",
        "base_id",
    )
    write_once_fields = ("source_task_id", "expired_at")
    session_id = models.UUIDField(unique=True)
    owner_id = models.UUIDField()
    base = models.ForeignKey("AppliedConfigurationVersion", on_delete=models.PROTECT)
    state = models.CharField(max_length=16, default="collecting")
    source_task = models.OneToOneField(
        "stewardship_jobs.TaskRun", null=True, on_delete=models.PROTECT
    )
    renewed_at = UTCDateTimeField(null=True)
    expired_at = UTCDateTimeField(null=True)
    expiry_reason = models.CharField(max_length=16, default="")

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_setup_attempt"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                models.Value(1),
                condition=models.Q(state__in=["collecting", "loading", "frozen"]),
                name="setup_one_pending_attempt",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=[
                        "collecting",
                        "loading",
                        "frozen",
                        "completed",
                        "expired",
                    ]
                ),
                name="setup_known_state",
            ),
            models.CheckConstraint(
                condition=~models.Q(state="loading")
                | models.Q(source_task__isnull=False),
                name="setup_loading_task",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state="expired",
                    expired_at__isnull=False,
                    expiry_reason__in=[
                        "cancelled",
                        "session",
                        "idle",
                        "watchdog",
                        "absolute",
                    ],
                )
                | (
                    ~models.Q(state="expired")
                    & models.Q(expired_at__isnull=True, expiry_reason="")
                ),
                name="setup_expiry_shape",
            ),
        ]


class SetupDraftSection(MutableRecord):
    """Temporary public settings, scrubbed atomically when their attempt expires."""

    immutable_fields = MutableRecord.immutable_fields + ("attempt_id", "step")
    write_once_fields = ("scrubbed_at",)
    attempt = models.ForeignKey(SetupAttempt, on_delete=models.PROTECT)
    step = models.CharField(max_length=48)
    values = models.JSONField()
    # Invoker-only delivery guards compare settings without exposing draft JSON
    # to cleanup or unrelated credential identities. PostgreSQL owns the value.
    scope_digest = models.GeneratedField(
        expression=models.Func(
            models.F("values"),
            template=(
                "pg_catalog.encode(pg_catalog.sha256("
                "pg_catalog.jsonb_send(%(expressions)s)), 'hex')"
            ),
        ),
        output_field=models.CharField(max_length=64),
        db_persist=True,
    )
    scrubbed_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_setup_draft_section"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["attempt", "step"], name="setup_one_draft_per_step"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    step__in=[
                        "parish",
                        "branding",
                        "access",
                        "mail",
                        "slack",
                        "testing",
                        "campaign",
                        "schedules",
                        *CONTENT_STEPS,
                    ]
                ),
                name="setup_public_step",
            ),
            models.CheckConstraint(
                condition=models.Q(scrubbed_at__isnull=True) | models.Q(values={}),
                name="setup_scrubbed_values_empty",
            ),
        ]


class SetupConfigurationIntent(ImmutableRecord):
    """One frozen original-login attempt owns exactly one finalization request."""

    attempt = models.OneToOneField(SetupAttempt, on_delete=models.PROTECT)
    request = models.OneToOneField(
        "ConfigurationChangeRequest", on_delete=models.PROTECT
    )
    attempt_version = models.PositiveBigIntegerField()

    class Meta:
        db_table = "stewardship_setup_config_intent"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(attempt_version__gte=1),
                name="setup_intent_positive_version",
            ),
        ]


class SetupConfigurationAbort(ImmutableRecord):
    """Durable cancellation proof precedes restoration of unapplied setup YAML."""

    intent = models.OneToOneField(SetupConfigurationIntent, on_delete=models.PROTECT)
    reason = models.CharField(max_length=16)

    class Meta:
        db_table = "stewardship_setup_config_abort"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    reason__in=["cancelled", "session", "idle", "watchdog", "absolute"]
                ),
                name="setup_abort_reason",
            ),
        ]
