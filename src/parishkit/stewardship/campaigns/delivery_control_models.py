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


class HeldMessageResolution(ImmutableRecord):
    """Exact closed-message decision, owned only by the private command effect."""

    command = models.ForeignKey(DeliveryControlCommand, on_delete=models.PROTECT)
    message = models.ForeignKey(
        "stewardship_jobs.OutboxMessage", on_delete=models.PROTECT
    )
    campaign = models.ForeignKey("Campaign", on_delete=models.PROTECT)
    previous_version = models.PositiveBigIntegerField()
    pause_version = models.PositiveBigIntegerField()
    decision = models.CharField(max_length=8)

    class Meta:
        db_table = "stewardship_delivery_message_resolution"
        constraints = [
            models.UniqueConstraint(
                fields=("command", "message"), name="held_resolution_command"
            ),
            models.UniqueConstraint(
                fields=("message", "pause_version"), name="held_resolution_pause"
            ),
            models.CheckConstraint(
                condition=models.Q(previous_version__gte=1)
                & models.Q(pause_version__gte=1),
                name="held_resolution_versions",
            ),
            models.CheckConstraint(
                condition=models.Q(decision__in=["release", "cancel"]),
                name="held_resolution_decision",
            ),
        ]
