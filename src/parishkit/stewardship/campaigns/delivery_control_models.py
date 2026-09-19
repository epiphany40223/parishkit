"""Exact, immutable Admin delivery-control intent, separate from worker history."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord, UTCDateTimeField


class DeliveryControlCommand(ImmutableRecord):
    """Bind one preview and fresh session to its atomic pause/recovery effect."""

    campaign = models.ForeignKey("Campaign", on_delete=models.PROTECT)
    control = models.OneToOneField(
        "CampaignControlChange", on_delete=models.PROTECT, null=True
    )
    action = models.CharField(max_length=24)
    session_id = models.UUIDField()
    authenticated_at = UTCDateTimeField()
    preview_at = UTCDateTimeField()
    expires_at = UTCDateTimeField()
    expected_campaign_version = models.PositiveBigIntegerField()
    expected_runtime_version = models.PositiveBigIntegerField()
    inventory = models.JSONField()
    selection = models.JSONField(default=dict)
    reason = models.CharField(max_length=1024)

    class Meta:
        db_table = "stewardship_delivery_control"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(expected_campaign_version__gte=1)
                & models.Q(expected_runtime_version__gte=1),
                name="delivery_control_versions",
            ),
            models.CheckConstraint(
                condition=models.Q(action__in=["pause", "resume", "resolve"]),
                name="delivery_control_action",
            ),
        ]
