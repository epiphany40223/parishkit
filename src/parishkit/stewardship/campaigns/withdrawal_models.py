"""Immutable pre-start withdrawal intent and its atomic lifecycle result."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord, UTCDateTimeField


class ProductionWithdrawal(ImmutableRecord):
    """One withdrawal per activation; prior cleanup and activation remain history."""

    confirmation = models.OneToOneField(
        "ProductionConfirmation", on_delete=models.PROTECT
    )
    transition = models.OneToOneField("CampaignTransition", on_delete=models.PROTECT)
    request_key = models.UUIDField(unique=True)
    session_id = models.UUIDField()
    authenticated_at = UTCDateTimeField()
    preview_at = UTCDateTimeField()
    expires_at = UTCDateTimeField()
    expected_campaign_version = models.PositiveBigIntegerField()
    expected_runtime_version = models.PositiveBigIntegerField()
    inventory = models.JSONField()
    reason = models.CharField(max_length=2000)
    cleanup_acknowledged = models.BooleanField()

    class Meta:
        db_table = "stewardship_production_withdrawal"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(expected_campaign_version__gte=1)
                & models.Q(expected_runtime_version__gte=1),
                name="production_withdrawal_versions",
            ),
            models.CheckConstraint(
                condition=models.Q(cleanup_acknowledged=True),
                name="production_withdrawal_acknowledged",
            ),
        ]
