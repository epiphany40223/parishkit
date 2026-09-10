"""Immutable campaign configuration and the guarded Testing runtime subset.

Production lifecycle mutations require future readiness/workflow migrations.
Keeping the subset constrained in PostgreSQL prevents scaffolding from becoming
an accidental route around those dependencies.
"""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord, MutableRecord


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
    """Stable campaign UUID; configuration activation is the only current writer."""

    immutable_fields = MutableRecord.immutable_fields + ("state",)
    state = models.CharField(max_length=24, default="draft")
    active_configuration = models.ForeignKey(
        CampaignConfiguration, on_delete=models.PROTECT
    )

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_campaign"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(state="draft"), name="campaign_draft_subset"
            ),
            models.UniqueConstraint(models.Value(1), name="campaign_one_draft_subset"),
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
