"""Append-only Family-scoped refusal evidence and separately recorded resolution."""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord


class RecipientRefusal(ImmutableRecord):
    """One refused address from one exact Production delivery outcome.

    A refusal never suppresses another Family that happens to share the address.
    SQL verifies the immutable delivery event and its exact routed recipients.
    UUID references keep baseline model ordering separate from runtime proof.

    ``kind`` is ``provider`` for a refusal the mail service gave, or
    ``reported_bounce`` for an Administrator's report that a delivered email
    bounced at the address (#806 slice 4, migration 0081), which names the
    email's delivered event and keeps the Administrator's ``evidence_note``.
    Both kinds set the address aside alike.
    """

    family_id = models.UUIDField()
    organization_id = models.PositiveBigIntegerField()
    family_duid = models.PositiveBigIntegerField()
    event_id = models.UUIDField()
    address = models.CharField(max_length=254)
    kind = models.CharField(max_length=32, default="provider", db_default="provider")
    evidence_note = models.CharField(
        max_length=2000, blank=True, default="", db_default=""
    )

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
            models.CheckConstraint(
                condition=models.Q(kind="provider", evidence_note="")
                | (
                    models.Q(kind="reported_bounce", actor_id__isnull=False)
                    & ~models.Q(evidence_note="")
                ),
                name="recipient_refusal_kind",
            ),
        ]


class RecipientRefusalResolution(ImmutableRecord):
    """Clearing preserves original refusal evidence rather than deleting it."""

    refusal_id = models.UUIDField(unique=True)
    source_snapshot_id = models.UUIDField()
    source_generation = models.PositiveBigIntegerField()
    reason = models.CharField(max_length=32, default="source_changed")
    evidence_note = models.CharField(max_length=2000, blank=True, default="")

    class Meta:
        db_table = "stewardship_recipient_resolution"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(reason="source_changed", evidence_note="")
                | (
                    models.Q(reason="verified_admin", actor_id__isnull=False)
                    & ~models.Q(evidence_note="")
                ),
                name="recipient_resolution_reason",
            ),
            models.CheckConstraint(
                condition=models.Q(source_generation__gt=0),
                name="recipient_resolution_generation",
            ),
        ]
