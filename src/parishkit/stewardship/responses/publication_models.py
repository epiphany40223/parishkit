"""Confirmed ParishSoft publication plans and their per-entity outcomes (#528).

DAT-09.01. A ``PublicationPlan`` is one preflight's result: who asked for it,
for which campaign, against which promoted source snapshot and expected
organization, and whether the system was in Testing mode. Its
``PublicationEntity`` rows hold each Member's plan (blocked or not, and the
SHA-256 of the contact payload read and of the payload planned), and its
``PublicationField`` rows each planned proposal with the version it had and
the planner's outcome (``responses.publication.Outcome``). An Administrator's
``PublicationConfirmation`` accepts the plan exactly as counted, and each
write attempt on an entity is one ``PublicationOutcome``.

Every row is append-only and holds no Member value: only identifiers, field
names, versions, counts and hashes. SQL checks every row
(``schema/migrations/0087_publication_plans.sql``); the services are in
``responses.publication_plans``.
"""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord, UTCDateTimeField

# The planner's field outcomes (responses.publication.Outcome), and what one
# write attempt on an entity came to.
PLAN_OUTCOMES = (
    "write",
    "already_in_parishsoft",
    "conflict",
    "cannot_check",
    "not_approved",
    "by_hand",
)
ENTITY_RESULTS = ("published", "invalidated", "failed")
SHA256 = r"^[0-9a-f]{64}$"
FAILURE_KIND = r"^[a-z][a-z0-9_]{0,39}$"


class PublicationPlan(ImmutableRecord):
    """One preflight's plan, persisted for confirmation (#528, DAT-09.01).

    ``actor_id`` is the Administrator who asked for the preflight. The
    database stamps ``testing`` from the system mode, and a Testing plan is
    never confirmed: in Testing mode publication stops after preflight.
    """

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT, related_name="+"
    )
    source_snapshot = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT, related_name="+"
    )
    organization_id = models.PositiveBigIntegerField()
    testing = models.BooleanField()
    request_key = models.UUIDField()

    class Meta:
        db_table = "stewardship_publication_plan"
        constraints = [
            models.UniqueConstraint(
                fields=("actor_id", "request_key"), name="publication_plan_replay"
            ),
            models.CheckConstraint(
                condition=models.Q(actor_id__isnull=False),
                name="publication_plan_actor",
            ),
        ]


class PublicationEntity(ImmutableRecord):
    """One entity's plan: blocked or not, and the read and planned payload hashes.

    The hashes are present together, and only when the entity is not blocked
    and has at least one field to write. They let the worker refuse a PUT
    whose fresh read no longer matches what the Administrator confirmed.
    """

    plan = models.ForeignKey(
        PublicationPlan, on_delete=models.PROTECT, related_name="entities"
    )
    entity_kind = models.CharField(max_length=20)
    entity_key = models.CharField(max_length=36)
    blocked = models.BooleanField()
    read_sha256 = models.CharField(max_length=64, null=True)
    planned_sha256 = models.CharField(max_length=64, null=True)

    class Meta:
        db_table = "stewardship_publication_entity"
        constraints = [
            models.UniqueConstraint(
                fields=("plan", "entity_kind", "entity_key"),
                name="publication_entity_identity",
            ),
            models.CheckConstraint(
                condition=models.Q(entity_kind__in=("member", "family"))
                & ~models.Q(entity_key=""),
                name="publication_entity_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    read_sha256__isnull=True, planned_sha256__isnull=True
                )
                | models.Q(
                    blocked=False,
                    read_sha256__regex=SHA256,
                    planned_sha256__regex=SHA256,
                ),
                name="publication_entity_hashes",
            ),
        ]


class PublicationField(ImmutableRecord):
    """One proposal in an entity's plan, at the version it was planned from."""

    entity = models.ForeignKey(
        PublicationEntity, on_delete=models.PROTECT, related_name="planned_fields"
    )
    proposal = models.ForeignKey(
        "stewardship_responses.ProposedChange",
        on_delete=models.PROTECT,
        related_name="+",
    )
    proposal_version = models.PositiveBigIntegerField()
    field = models.CharField(max_length=40)
    outcome = models.CharField(max_length=24)

    class Meta:
        db_table = "stewardship_publication_field"
        constraints = [
            models.UniqueConstraint(
                fields=("entity", "proposal"), name="publication_field_identity"
            ),
            models.CheckConstraint(
                condition=models.Q(proposal_version__gte=1, outcome__in=PLAN_OUTCOMES),
                name="publication_field_shape",
            ),
        ]


class PublicationConfirmation(ImmutableRecord):
    """An Administrator's final, freshly signed-in confirmation of one plan.

    The counts are what the Administrator was shown; SQL requires them to
    equal the plan's, so the confirmation covers exactly that plan.
    """

    plan = models.OneToOneField(
        PublicationPlan, on_delete=models.PROTECT, related_name="confirmation"
    )
    session_id = models.UUIDField()
    authenticated_at = UTCDateTimeField()
    request_key = models.UUIDField()
    write_entities = models.PositiveIntegerField()
    write_fields = models.PositiveIntegerField()
    blocked_entities = models.PositiveIntegerField()

    class Meta:
        db_table = "stewardship_publication_confirmation"
        constraints = [
            models.UniqueConstraint(
                fields=("actor_id", "request_key"),
                name="publication_confirmation_replay",
            ),
            models.CheckConstraint(
                condition=models.Q(actor_id__isnull=False, write_entities__gte=1),
                name="publication_confirmation_shape",
            ),
        ]


class PublicationOutcome(ImmutableRecord):
    """One write attempt on a confirmed entity, under the source mutation fence.

    ``published``: written and verified by reading it back. ``invalidated``:
    not written, since the fresh read or the proposals no longer match the
    confirmed plan. ``failed``: the write or its verification failed, with a
    sanitized ``failure_kind``; the entity stays retryable. Nothing follows a
    ``published`` or ``invalidated`` outcome, so a success is never replayed.
    """

    entity = models.ForeignKey(
        PublicationEntity, on_delete=models.PROTECT, related_name="outcomes"
    )
    attempt = models.PositiveIntegerField()
    result = models.CharField(max_length=12)
    failure_kind = models.CharField(max_length=40, blank=True, default="")
    fence = models.PositiveBigIntegerField()

    class Meta:
        db_table = "stewardship_publication_outcome"
        constraints = [
            models.UniqueConstraint(
                fields=("entity", "attempt"), name="publication_outcome_attempt"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    attempt__gte=1, fence__gte=1, result__in=ENTITY_RESULTS
                ),
                name="publication_outcome_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(result="failed", failure_kind__regex=FAILURE_KIND)
                | (~models.Q(result="failed") & models.Q(failure_kind="")),
                name="publication_outcome_failure",
            ),
        ]
