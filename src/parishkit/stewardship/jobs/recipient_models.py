"""Append-only Family-scoped refusal evidence and separately recorded resolution."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord


class RecipientRefusal(ImmutableRecord):
    """One refused address from one exact Production delivery outcome.

    A refusal never suppresses another Family that happens to share the address.
    SQL verifies the immutable delivery event and its exact routed recipients.
    UUID references keep baseline model ordering separate from runtime proof.
    """

    family_id = models.UUIDField()
    organization_id = models.PositiveBigIntegerField()
    family_duid = models.PositiveBigIntegerField()
    event_id = models.UUIDField()
    address = models.CharField(max_length=254)

    class Meta:
        db_table = "stewardship_recipient_refusal"
        indexes = [
            models.Index(fields=["family_id"], name="recipient_refusal_family"),
            models.Index(
                fields=["organization_id", "family_duid"],
                name="recipient_refusal_identity",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["event_id", "address"], name="recipient_refusal_event_address"
            ),
            models.CheckConstraint(
                condition=models.Q(organization_id__gt=0) & models.Q(family_duid__gt=0),
                name="recipient_refusal_identity_positive",
            ),
        ]


class RecipientRefusalResolution(ImmutableRecord):
    """Clearing preserves original refusal evidence rather than deleting it."""

    refusal_id = models.UUIDField(unique=True)
    source_snapshot_id = models.UUIDField()
    source_generation = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=32, default="source_changed")

    class Meta:
        db_table = "stewardship_recipient_resolution"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(reason="source_changed"),
                name="recipient_resolution_reason",
            ),
            models.CheckConstraint(
                condition=models.Q(source_generation__gt=0),
                name="recipient_resolution_generation",
            ),
        ]
