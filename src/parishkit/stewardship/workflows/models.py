"""Durable Ministry intent, separate from immutable Family answers and rosters."""

from django.db import models

from parishkit.stewardship.storage import MutableRecord, UTCDateTimeField


class MinistryRequest(MutableRecord):
    """One requested roster action with retained response and outcome provenance.

    Existing Members use canonical source DUIDs; proposed Members use the local
    UUID from their complete census answer. Neither identity is a provider write
    instruction. Contact/assignment editing belongs to the later Staff owner.
    """

    submission = models.ForeignKey(
        "stewardship_responses.Submission", on_delete=models.PROTECT
    )
    entity_kind = models.CharField(max_length=20)
    entity_key = models.CharField(max_length=36)
    ministry_duid = models.PositiveIntegerField()
    action = models.CharField(max_length=5)
    state = models.CharField(max_length=20, default="new")
    outcome = models.CharField(max_length=20, null=True)
    resolved_at = UTCDateTimeField(null=True)
    resolution_source = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT, null=True
    )
    superseded_by = models.ForeignKey("self", on_delete=models.PROTECT, null=True)

    immutable_fields = MutableRecord.immutable_fields + (
        "submission_id",
        "entity_kind",
        "entity_key",
        "ministry_duid",
        "action",
    )

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_ministry_request"
        indexes = [
            models.Index(
                fields=["ministry_duid", "state"], name="ministry_request_queue"
            )
        ]
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["submission", "entity_kind", "entity_key", "ministry_duid"],
                name="ministry_request_identity",
            ),
            models.CheckConstraint(
                condition=models.Q(entity_kind__in=["member", "proposed_member"])
                & ~models.Q(entity_key="")
                & models.Q(ministry_duid__gt=0, ministry_duid__lt=2**31),
                name="ministry_request_entity",
            ),
            models.CheckConstraint(
                condition=models.Q(action__in=["join", "leave"])
                & ~models.Q(entity_kind="proposed_member", action="leave"),
                name="ministry_request_action",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=[
                        "new",
                        "assigned",
                        "in_progress",
                        "resolved",
                        "closed_no_response",
                        "cancelled",
                        "superseded",
                    ]
                ),
                name="ministry_request_state",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(state__in=["resolved", "closed_no_response"])
                    & models.Q(resolved_at__isnull=False, outcome__isnull=False)
                    & models.Q(
                        outcome__in=[
                            "joined",
                            "leave_confirmed",
                            "declined",
                            "no_response",
                            "duplicate",
                            "other",
                        ]
                    )
                )
                | (
                    ~models.Q(state__in=["resolved", "closed_no_response"])
                    & models.Q(
                        resolved_at__isnull=True,
                        outcome__isnull=True,
                        resolution_source__isnull=True,
                    )
                ),
                name="ministry_request_outcome",
            ),
            models.CheckConstraint(
                condition=models.Q(state="superseded", superseded_by__isnull=False)
                | (
                    ~models.Q(state="superseded") & models.Q(superseded_by__isnull=True)
                ),
                name="ministry_request_successor",
            ),
            models.CheckConstraint(
                condition=~models.Q(superseded_by_id=models.F("id")),
                name="ministry_request_not_own_next",
            ),
        ]
