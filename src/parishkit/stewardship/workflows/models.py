"""Durable Ministry intent, separate from immutable Family answers and rosters."""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)

# Families own cancellation/supersession and the source worker owns roster-
# evidence resolution; Staff edits move a request only among these states.
# `assigned` stays a stored, open state for requests assigned before
# follow-up assignment was removed (#552), but no edit chooses it any more.
STAFF_STATES = ("new", "in_progress", "resolved", "closed_no_response")
OPEN_STATES = ("new", "assigned", "in_progress")
RESOLVED_OUTCOMES = ("joined", "leave_confirmed", "declined", "duplicate", "other")
# The outcomes that accept a request (the review pipeline's Accept): the
# Member joins or leaves. Every other outcome, and Closed: no response, is
# Ignored. Only these carry an Edit's Ministry.
ACCEPTED_OUTCOMES = ("joined", "leave_confirmed")
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
    # Unused since follow-up assignment was removed (#552): Staff edits store
    # none, and only requests assigned before then still carry one.
    assignee_id = models.UUIDField(null=True)
    # The review pipeline (#1684). An Edit's Ministry, carried forward in
    # place of the Family's (``ministry_duid`` stays as answered); set only
    # while the request is accepted, and cleared by Back to review.
    reviewed_ministry_duid = models.IntegerField(null=True)
    # A request Staff added with no Family answer behind it, attached to the
    # Family's latest live response, and the same Member's ignored request it
    # was added in place of, if any.
    parish_added = models.BooleanField(default=False, db_default=False)
    added_in_place_of = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, related_name="+"
    )

    immutable_fields = MutableRecord.immutable_fields + (
        "submission_id",
        "entity_kind",
        "entity_key",
        "ministry_duid",
        "action",
        "parish_added",
        "added_in_place_of_id",
    )

    @property
    def current_ministry_duid(self):
        """The Ministry the request is about now: an Edit's, else the Family's."""
        return self.reviewed_ministry_duid or self.ministry_duid

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
            # An Edit's Ministry only on an accepted request, never its own.
            models.CheckConstraint(
                condition=models.Q(reviewed_ministry_duid__isnull=True)
                | (
                    models.Q(
                        reviewed_ministry_duid__gt=0,
                        state="resolved",
                        outcome__in=list(ACCEPTED_OUTCOMES),
                    )
                    & ~models.Q(reviewed_ministry_duid=models.F("ministry_duid"))
                ),
                name="ministry_request_reviewed",
            ),
            # Only an existing Member's request is added, and only an added
            # request names the one it replaces (never itself).
            models.CheckConstraint(
                condition=(
                    ~models.Q(parish_added=True) | models.Q(entity_kind="member")
                )
                & (
                    models.Q(added_in_place_of__isnull=True)
                    | (
                        models.Q(parish_added=True)
                        & ~models.Q(added_in_place_of=models.F("id"))
                    )
                ),
                name="ministry_request_added",
            ),
        ]


class MinistryWorkflowRevision(ImmutableRecord):
    """One authorized Staff edit: the complete resulting workflow, never a diff.

    The request keeps the current state/outcome; this history owns the
    notes and contact attempts. SQL stamps the time, validates current authority
    and pairs each revision with its projection and audit. Notes and contact
    details are private workflow data and never enter audit context or logs.
    """

    request = models.ForeignKey(
        MinistryRequest, on_delete=models.PROTECT, related_name="revisions"
    )
    expected_version = models.PositiveBigIntegerField()
    request_key = models.UUIDField()
    # Set only on revisions recorded before assignment was removed (#552).
    assignee_id = models.UUIDField(null=True)
    state = models.CharField(max_length=20)
    outcome = models.CharField(max_length=20, null=True)
    notes = models.TextField(default="", blank=True)
    contact_channel = models.CharField(max_length=10, null=True)
    contact_at = UTCDateTimeField(null=True)
    contact_notes = models.TextField(default="", blank=True)
    # The review pipeline (#1684): the Ministry an Edit carried forward, and
    # the optional note of the decision this revision records.
    reviewed_ministry_duid = models.IntegerField(null=True)
    decision_note = models.TextField(default="", db_default="", blank=True)

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
                    # A cancelled revision withdraws a request the parish
                    # added (#1684).
                    models.Q(
                        state__in=[*OPEN_STATES, "cancelled"], outcome__isnull=True
                    )
                    | models.Q(state="resolved", outcome__in=list(RESOLVED_OUTCOMES))
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
                    contact_channel__in=list(CONTACT_CHANNELS), contact_at__isnull=False
                ),
                name="ministry_revision_contact",
            ),
            models.CheckConstraint(
                condition=models.Q(reviewed_ministry_duid__isnull=True)
                | models.Q(
                    reviewed_ministry_duid__gt=0,
                    state="resolved",
                    outcome__in=list(ACCEPTED_OUTCOMES),
                ),
                name="ministry_revision_reviewed",
            ),
        ]


class MinistryRosterEntry(ImmutableRecord):
    """One set or clear of a resolved request's Entered in ParishSoft tick (#528).

    The ParishSoft API cannot change Ministry rosters, so a join or leave a
    person resolved is entered in ParishSoft by hand. Staff and Admin record
    that here; the current state is the latest row (none means not entered).
    The request itself is a closed outcome and never changes. SQL checks the
    actor, the request, the sequence and the audit
    (schema/migrations/0061_roster_entries.sql).
    """

    request = models.ForeignKey(
        MinistryRequest, on_delete=models.PROTECT, related_name="roster_entries"
    )
    sequence = models.PositiveBigIntegerField()
    entered = models.BooleanField()
    request_key = models.UUIDField()

    class Meta:
        db_table = "stewardship_ministry_roster_entry"
        constraints = [
            models.UniqueConstraint(
                fields=("request", "sequence"), name="ministry_roster_sequence"
            ),
            models.UniqueConstraint(
                fields=("actor_id", "request_key"), name="ministry_roster_replay"
            ),
            models.CheckConstraint(
                condition=models.Q(actor_id__isnull=False, sequence__gte=1),
                name="ministry_roster_identity",
            ),
        ]


# What one promoted refresh found for one item whose own row cannot carry
# the outcome (#1689; review pipeline spec, "Refresh checks").
REFRESH_OUTCOMES = (
    "confirmed",
    "unchanged",
    "entered_incorrectly",
    "ignored_now_matches",
    # An Updated in ParishSoft change ParishSoft no longer holds (#1749):
    # the check that moves it to Conflict.
    "conflict",
)


class RefreshCheck(ImmutableRecord):
    """One refresh's outcome for a final census change or a decided request.

    A census change Entered by hand or Updated in ParishSoft is final in its
    guard, and a Ministry request a person decided changes only by review,
    so the baseline refresh re-evaluation records what each refresh found
    here instead (#1689): the outcome, ParishSoft's value for a census
    change (``observed``, ``{"available", "value"}``) and whether the
    refresh was full. The item's stage is derived from its latest check.
    Exactly one of ``proposal`` and ``request`` is set. Only the source
    worker writes one, inside the promotion of ``snapshot_id`` and for the
    item at ``item_version``; SQL checks each of these and keeps the rows
    immutable (schema/migrations/0136_refresh_checks.sql).
    """

    snapshot_id = models.UUIDField()
    full_refresh = models.BooleanField()
    proposal = models.ForeignKey(
        "stewardship_responses.ProposedChange",
        on_delete=models.PROTECT,
        null=True,
        related_name="+",
    )
    request = models.ForeignKey(
        MinistryRequest, on_delete=models.PROTECT, null=True, related_name="+"
    )
    item_version = models.PositiveBigIntegerField()
    outcome = models.CharField(max_length=24)
    observed = models.JSONField(null=True)

    class Meta:
        db_table = "stewardship_refresh_check"
        constraints = [
            models.UniqueConstraint(
                fields=("snapshot_id", "proposal"), name="refresh_check_proposal_once"
            ),
            models.UniqueConstraint(
                fields=("snapshot_id", "request"), name="refresh_check_request_once"
            ),
            models.CheckConstraint(
                condition=models.Q(outcome__in=REFRESH_OUTCOMES),
                name="refresh_check_outcome",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(proposal__isnull=False, request__isnull=True)
                    | models.Q(proposal__isnull=True, request__isnull=False)
                ),
                name="refresh_check_one_item",
            ),
            models.CheckConstraint(
                condition=models.Q(actor_id__isnull=True, item_version__gte=1),
                name="refresh_check_identity",
            ),
        ]


class RefreshCheckStart(models.Model):
    """When Not seen in ParishSoft starts counting, and after how many refreshes.

    One row, written once by migration 0136, audited on an upgrade (#1689): Not
    seen counts only the full refreshes recorded after ``started_at``, so
    marks made before the release are not all flagged by its first refresh.
    ``not_seen_after`` is the spec's configurable number of full refreshes
    (default two). Deleting the row turns the flag off.
    """

    singleton = models.BooleanField(primary_key=True, default=True)
    started_at = UTCDateTimeField()
    not_seen_after = models.PositiveSmallIntegerField(default=2)

    class Meta:
        db_table = "stewardship_refresh_check_start"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    singleton=True, not_seen_after__gte=1, not_seen_after__lte=10
                ),
                name="refresh_check_start_one",
            ),
        ]
