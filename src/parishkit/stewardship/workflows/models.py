"""Durable Ministry intent, separate from immutable Family answers and rosters."""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)

# Families own cancellation/supersession and the source worker owns roster-
# evidence resolution; Staff edits move a request only among these states.
STAFF_STATES = ("new", "assigned", "in_progress", "resolved", "closed_no_response")
OPEN_STATES = ("new", "assigned", "in_progress")
RESOLVED_OUTCOMES = ("joined", "leave_confirmed", "declined", "duplicate", "other")
CONTACT_CHANNELS = ("email", "phone", "in_person", "other")


class MinistryRequest(MutableRecord):
    """One requested roster action with retained response and outcome provenance.

    Existing Members use canonical source DUIDs; proposed Members use the local
    UUID from their complete census answer. Neither identity is a provider write
    instruction. Staff edits change only the current workflow projection here;
    `MinistryWorkflowRevision` owns their notes, contact attempts and history.
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
    # A plain UUID, like other retained actor references: history must outlive
    # a portal user. A same-intent successor inherits it with the state.
    assignee_id = models.UUIDField(null=True)

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
            # Closed, cancelled and superseded rows retain who held the work.
            models.CheckConstraint(
                condition=(~models.Q(state="new") | models.Q(assignee_id__isnull=True))
                & (~models.Q(state="assigned") | models.Q(assignee_id__isnull=False)),
                name="ministry_request_assignment",
            ),
        ]


class MinistryWorkflowRevision(ImmutableRecord):
    """One authorized Staff edit: the complete resulting workflow, never a diff.

    The request keeps the current assignee/state/outcome; this history owns the
    notes and contact attempts. SQL stamps the time, validates current authority
    and pairs each revision with its projection and audit. Notes and contact
    details are private workflow data and never enter audit context or logs.
    """

    request = models.ForeignKey(
        MinistryRequest, on_delete=models.PROTECT, related_name="revisions"
    )
    expected_version = models.PositiveBigIntegerField()
    request_key = models.UUIDField()
    assignee_id = models.UUIDField(null=True)
    state = models.CharField(max_length=20)
    outcome = models.CharField(max_length=20, null=True)
    notes = models.TextField(default="", blank=True)
    contact_channel = models.CharField(max_length=10, null=True)
    contact_at = UTCDateTimeField(null=True)
    contact_notes = models.TextField(default="", blank=True)

    class Meta:
        db_table = "stewardship_ministry_revision"
        constraints = [
            models.UniqueConstraint(
                fields=("request", "expected_version"), name="ministry_revision_version"
            ),
            models.UniqueConstraint(
                fields=("actor_id", "request_key"), name="ministry_revision_replay"
            ),
            models.CheckConstraint(
                condition=models.Q(expected_version__gte=1, actor_id__isnull=False),
                name="ministry_revision_identity",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(state__in=OPEN_STATES, outcome__isnull=True)
                    | models.Q(state="resolved", outcome__in=RESOLVED_OUTCOMES)
                    | models.Q(state="closed_no_response", outcome="no_response")
                )
                # "Other" is meaningless in a packet without its explanation.
                & (~models.Q(outcome="other") | ~models.Q(notes="")),
                name="ministry_revision_outcome",
            ),
            models.CheckConstraint(
                condition=(~models.Q(state="new") | models.Q(assignee_id__isnull=True))
                & (~models.Q(state="assigned") | models.Q(assignee_id__isnull=False)),
                name="ministry_revision_assignment",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    contact_channel__isnull=True,
                    contact_at__isnull=True,
                    contact_notes="",
                )
                | models.Q(
                    contact_channel__in=CONTACT_CHANNELS, contact_at__isnull=False
                ),
                name="ministry_revision_contact",
            ),
        ]
