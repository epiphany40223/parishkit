"""Immutable campaign configuration and the guarded Testing runtime subset.

Production lifecycle mutations require future readiness/workflow migrations.
Keeping the subset constrained in PostgreSQL prevents scaffolding from becoming
an accidental route around those dependencies.
"""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)


class CampaignConfiguration(ImmutableRecord):
    """Exact YAML projection with indexed identity/date columns and immutable values."""

    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion",
        on_delete=models.PROTECT,
        related_name="campaign_configurations",
    )
    record_id = models.UUIDField(db_index=True)
    name = models.CharField(max_length=254)
    timezone = models.CharField(max_length=254)
    start_date = models.DateField()
    end_date = models.DateField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    values = models.JSONField()

    class Meta:
        db_table = "stewardship_campaign_configuration"
        constraints = [
            models.UniqueConstraint(
                fields=["configuration", "record_id"],
                name="campaign_projection_identity",
            ),
            models.UniqueConstraint(
                fields=["configuration", "name"], name="campaign_projection_name"
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F("start_date")),
                name="campaign_ordered_dates",
            ),
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="campaign_ordered_instants",
            ),
        ]


class Campaign(MutableRecord):
    """Stable UUID with configuration projections and separately ledgered runtime."""

    immutable_fields = MutableRecord.immutable_fields
    state = models.CharField(max_length=24, default="draft")
    active_configuration = models.ForeignKey(
        CampaignConfiguration, on_delete=models.PROTECT
    )
    structural_locked = models.BooleanField(default=False, db_default=False)
    ever_active = models.BooleanField(default=False, db_default=False)
    first_live_delivery_at = UTCDateTimeField(null=True)
    first_live_submission_at = UTCDateTimeField(null=True)
    delivery_paused = models.BooleanField(default=False, db_default=False)
    pause_version = models.PositiveBigIntegerField(default=0, db_default=0)
    pause_actor_id = models.UUIDField(null=True)
    pause_reason = models.CharField(max_length=1024, default="", db_default="")
    paused_at = UTCDateTimeField(null=True)
    resumed_at = UTCDateTimeField(null=True)
    active_token_generation_id = models.UUIDField(null=True)
    readiness_revision = models.PositiveBigIntegerField(default=0, db_default=0)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_campaign"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(
                    state__in=[
                        "draft",
                        "scheduled",
                        "active",
                        "closed",
                        "archived",
                        "purging",
                        "purge_cleanup_failed",
                        "purged",
                    ]
                ),
                name="campaign_known_state",
            ),
            models.UniqueConstraint(
                models.Value(1),
                condition=models.Q(
                    state__in=["draft", "scheduled", "active", "closed"]
                ),
                name="campaign_one_current_state",
            ),
            models.CheckConstraint(
                condition=models.Q(ever_active=False)
                | models.Q(structural_locked=True),
                name="campaign_live_structure_locked",
            ),
            models.CheckConstraint(
                condition=models.Q(delivery_paused=False)
                | models.Q(
                    paused_at__isnull=False,
                    pause_actor_id__isnull=False,
                    pause_version__gt=0,
                ),
                name="campaign_pause_evidence",
            ),
        ]


class ScheduleRevision(ImmutableRecord):
    """Version-specific configuration; removal is absence in the new applied version.

    There is no runtime scheduler/occurrence admission yet. Logical definitions and
    fulfillment survive future revision/removal through their owning DAT-02/BG-04
    records, not by mutating these historical configuration rows.
    """

    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion",
        on_delete=models.PROTECT,
        related_name="schedule_revisions",
    )
    record_id = models.UUIDField(db_index=True)
    campaign_id = models.UUIDField(db_index=True)
    kind = models.CharField(max_length=24)
    due_at = models.DateTimeField(null=True)
    values = models.JSONField()

    class Meta:
        db_table = "stewardship_schedule_revision"
        constraints = [
            models.UniqueConstraint(
                fields=["configuration", "record_id"], name="schedule_revision_identity"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(kind__in=["initial", "reminder"], due_at__isnull=False)
                    | models.Q(
                        kind__in=["daily_digest", "weekly_digest"], due_at__isnull=True
                    )
                ),
                name="schedule_revision_due_kind",
            ),
        ]


# Django discovers models through this module; ownership stays in focused modules.
from .runtime_models import (  # noqa: E402,F401
    ActivationCatchUpDemand,
    CampaignBoundaryOccurrence,
    CampaignConfigurationAbort,
    CampaignConfigurationIntent,
    CampaignControlChange,
    CampaignTransition,
    CampaignWorkGate,
    CatchUpCheckpoint,
    RuntimeTransition,
)
from .schedule_models import (  # noqa: E402,F401
    OccurrenceTransition,
    PostCloseMailResolution,
    RestoreDeliveryHold,
    RestoreHoldResolution,
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
    ScheduleSelection,
)
