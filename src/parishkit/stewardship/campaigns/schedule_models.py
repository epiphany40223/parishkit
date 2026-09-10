"""Stable schedule identities, immutable semantic evidence and fenced occurrences."""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)

OCCURRENCE_STATES = (
    "pending",
    "running",
    "delivery_unknown",
    "succeeded",
    "skipped",
    "coalesced",
    "failed",
)


class ScheduleDefinition(MutableRecord):
    """A logical schedule survives configuration revisions, removal and retries."""

    immutable_fields = MutableRecord.immutable_fields + ("campaign_id", "kind")
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    kind = models.CharField(max_length=24)
    current_revision = models.ForeignKey(
        "stewardship_campaigns.ScheduleRevision", null=True, on_delete=models.PROTECT
    )
    removed_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_schedule_definition"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(
                    kind__in=["initial", "reminder", "daily_digest", "weekly_digest"]
                ),
                name="schedule_definition_kind",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(current_revision__isnull=False, removed_at__isnull=True)
                    | models.Q(current_revision__isnull=True, removed_at__isnull=False)
                ),
                name="schedule_selection_shape",
            ),
        ]


class ScheduleSelection(ImmutableRecord):
    """Applied replacement/removal marker; previous revisions remain immutable."""

    definition = models.ForeignKey(ScheduleDefinition, on_delete=models.PROTECT)
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    previous_revision = models.ForeignKey(
        "stewardship_campaigns.ScheduleRevision",
        null=True,
        on_delete=models.PROTECT,
        related_name="replacement_selections",
    )
    selected_revision = models.ForeignKey(
        "stewardship_campaigns.ScheduleRevision",
        null=True,
        on_delete=models.PROTECT,
        related_name="activation_selections",
    )
    version = models.PositiveBigIntegerField()

    class Meta:
        db_table = "stewardship_schedule_selection"
        constraints = [
            models.UniqueConstraint(
                fields=["definition", "version"], name="schedule_selection_version"
            ),
            models.UniqueConstraint(
                fields=["definition", "configuration"],
                name="schedule_selection_configuration",
            ),
        ]


class ScheduleOccurrence(MutableRecord):
    """Revision-specific execution identity; provider uncertainty is never terminal."""

    immutable_fields = MutableRecord.immutable_fields + (
        "definition_id",
        "revision_id",
        "mode",
        "routing",
        "target",
        "slot",
        "due_at",
        "occurrence_key",
    )
    definition = models.ForeignKey(ScheduleDefinition, on_delete=models.PROTECT)
    revision = models.ForeignKey(
        "stewardship_campaigns.ScheduleRevision", on_delete=models.PROTECT
    )
    mode = models.CharField(max_length=16)
    routing = models.CharField(max_length=24)
    target = models.CharField(max_length=128)
    slot = models.CharField(max_length=128)
    due_at = UTCDateTimeField()
    occurrence_key = models.CharField(max_length=64, unique=True)
    state = models.CharField(max_length=24, default="pending")
    task = models.ForeignKey(
        "stewardship_jobs.TaskRun", null=True, on_delete=models.PROTECT
    )
    outbox_id = models.UUIDField(null=True)
    worker_id = models.UUIDField(null=True)
    fence = models.PositiveBigIntegerField(default=0)
    attempts = models.PositiveBigIntegerField(default=0)
    lease_expires_at = UTCDateTimeField(null=True)
    heartbeat_at = UTCDateTimeField(null=True)
    reason = models.CharField(max_length=64, default="")
    replacement = models.ForeignKey("self", null=True, on_delete=models.PROTECT)
    pause_version = models.PositiveBigIntegerField(null=True)
    retry_command_id = models.UUIDField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_schedule_occurrence"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["revision", "mode", "target", "slot"],
                name="schedule_occurrence_semantic_revision",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=OCCURRENCE_STATES),
                name="schedule_occurrence_state",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(mode="production", routing="production")
                    | models.Q(mode="testing", routing="testing_override")
                ),
                name="schedule_occurrence_routing",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        state="running",
                        worker_id__isnull=False,
                        lease_expires_at__isnull=False,
                        heartbeat_at__isnull=False,
                        task__isnull=False,
                    )
                    | (
                        ~models.Q(state="running")
                        & models.Q(lease_expires_at__isnull=True)
                    )
                ),
                name="schedule_occurrence_lease",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(state="coalesced", replacement__isnull=False)
                    | (
                        ~models.Q(state="coalesced")
                        & models.Q(replacement__isnull=True)
                    )
                ),
                name="schedule_occurrence_replacement",
            ),
        ]
        indexes = [
            models.Index(fields=["state", "due_at"], name="schedule_occurrence_due")
        ]


class OccurrenceTransition(ImmutableRecord):
    """One immutable state/attempt/fencing record per occurrence version."""

    occurrence = models.ForeignKey(ScheduleOccurrence, on_delete=models.PROTECT)
    version = models.PositiveBigIntegerField()
    before_state = models.CharField(max_length=24, null=True)
    after_state = models.CharField(max_length=24)
    fence = models.PositiveBigIntegerField()
    attempts = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=64)
    retry_command_id = models.UUIDField(null=True)

    class Meta:
        db_table = "stewardship_occurrence_transition"
        constraints = [
            models.UniqueConstraint(
                fields=["occurrence", "version"], name="occurrence_history_version"
            ),
            models.UniqueConstraint(
                fields=["occurrence", "retry_command_id"],
                condition=models.Q(retry_command_id__isnull=False),
                name="occurrence_retry_command",
            ),
        ]


class ScheduleFulfillment(ImmutableRecord):
    """Semantic coverage across revisions, distinct from provider success."""

    definition = models.ForeignKey(ScheduleDefinition, on_delete=models.PROTECT)
    mode = models.CharField(max_length=16)
    target = models.CharField(max_length=128)
    slot = models.CharField(max_length=128)
    disposition = models.CharField(max_length=16)
    occurrence = models.ForeignKey(ScheduleOccurrence, on_delete=models.PROTECT)

    class Meta:
        db_table = "stewardship_schedule_fulfillment"
        constraints = [
            models.UniqueConstraint(
                fields=["definition", "mode", "target", "slot"],
                name="schedule_fulfillment_semantic",
            ),
            models.CheckConstraint(
                condition=models.Q(disposition__in=["delivered", "coalesced"]),
                name="schedule_fulfillment_disposition",
            ),
        ]


class RestoreDeliveryHold(MutableRecord):
    """Uncertainty inventory suppresses dispatch, without claiming fulfillment."""

    immutable_fields = MutableRecord.immutable_fields + (
        "restore_id",
        "definition_id",
        "mode",
        "target",
        "slot",
        "backup_at",
        "window_start",
        "window_end",
        "discovery",
    )
    restore_id = models.UUIDField()
    definition = models.ForeignKey(ScheduleDefinition, on_delete=models.PROTECT)
    mode = models.CharField(max_length=16)
    target = models.CharField(max_length=128)
    slot = models.CharField(max_length=128)
    backup_at = UTCDateTimeField()
    window_start = UTCDateTimeField()
    window_end = UTCDateTimeField()
    discovery = models.CharField(max_length=64)
    state = models.CharField(max_length=24, default="unreviewed")
    resolved_at = UTCDateTimeField(null=True)
    evidence = models.CharField(max_length=1024, default="")
    recovery_occurrence = models.ForeignKey(
        ScheduleOccurrence, null=True, on_delete=models.PROTECT
    )

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_restore_delivery_hold"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["restore_id", "definition", "mode", "target", "slot"],
                name="restore_hold_semantic",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=[
                        "unreviewed",
                        "assumed_delivered",
                        "resend_authorized",
                        "not_applicable",
                    ]
                ),
                name="restore_hold_state",
            ),
            models.CheckConstraint(
                condition=models.Q(window_end__gte=models.F("window_start")),
                name="restore_hold_window",
            ),
        ]


class RestoreHoldResolution(ImmutableRecord):
    """Append-only decisions retain correction history rather than erasing review."""

    hold = models.ForeignKey(RestoreDeliveryHold, on_delete=models.PROTECT)
    version = models.PositiveBigIntegerField()
    state = models.CharField(max_length=24)
    evidence = models.CharField(max_length=1024)
    recovery_occurrence = models.ForeignKey(
        ScheduleOccurrence, null=True, on_delete=models.PROTECT
    )

    class Meta:
        db_table = "stewardship_restore_hold_resolution"
        constraints = [
            models.UniqueConstraint(
                fields=["hold", "version"], name="restore_hold_resolution_version"
            )
        ]


class PostCloseMailResolution(ImmutableRecord):
    """An explicit skip covers exact versions, never later submissions/corrections."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    mode = models.CharField(max_length=16)
    obligation_key = models.CharField(max_length=256)
    coverage_digest = models.CharField(max_length=64)
    coverage = models.JSONField()
    reason = models.CharField(max_length=1024)
    occurrence = models.ForeignKey(
        ScheduleOccurrence, null=True, on_delete=models.PROTECT
    )
    task = models.ForeignKey(
        "stewardship_jobs.TaskRun", null=True, on_delete=models.PROTECT
    )
    outbox_id = models.UUIDField(null=True)

    class Meta:
        db_table = "stewardship_postclose_resolution"
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "mode", "obligation_key", "coverage_digest"],
                name="postclose_semantic_resolution",
            ),
            models.CheckConstraint(
                condition=models.Q(coverage_digest__regex=r"^[0-9a-f]{64}$"),
                name="postclose_coverage_digest",
            ),
        ]
