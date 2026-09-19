"""Inactive link preparation belongs to one cleaned-up go-live attempt.

These records bind intent and cancellation, not Production authority. A prepared
generation is unusable until the final campaign transition selects its pointer.
The general worker receives only public sealing keys and a durable task hint.
"""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord


class ProductionTokenPreparation(ImmutableRecord):
    """Freeze one source/configuration/key scope before any worker issues links."""

    transition = models.ForeignKey(
        "ProductionTransitionRequest", on_delete=models.PROTECT
    )
    task = models.OneToOneField("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    request_key = models.UUIDField()
    configuration = models.ForeignKey("CampaignConfiguration", on_delete=models.PROTECT)
    source_snapshot_id = models.UUIDField()
    source_generation = models.PositiveBigIntegerField()
    credential_epoch = models.UUIDField()
    key_inventory_digest = models.CharField(max_length=64)
    eligibility_digest = models.CharField(max_length=64)
    eligible_count = models.PositiveBigIntegerField()

    class Meta:
        db_table = "stewardship_production_tokens"
        constraints = [
            models.UniqueConstraint(
                fields=["transition", "request_key"],
                name="production_tokens_request_key",
            ),
            models.CheckConstraint(
                condition=models.Q(source_generation__gte=1),
                name="production_tokens_source_generation",
            ),
            models.CheckConstraint(
                condition=models.Q(key_inventory_digest__regex=r"^[0-9a-f]{64}$")
                & models.Q(eligibility_digest__regex=r"^[0-9a-f]{64}$"),
                name="production_tokens_digests",
            ),
        ]


class ProductionTokenCancellation(ImmutableRecord):
    """Cancellation immediately revokes admission; a worker disposes sealed staging."""

    preparation = models.OneToOneField(
        ProductionTokenPreparation, on_delete=models.PROTECT
    )
    task = models.OneToOneField("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    request_key = models.UUIDField(unique=True)

    class Meta:
        db_table = "stewardship_production_token_cancel"
