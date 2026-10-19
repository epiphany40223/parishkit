"""Immutable final answers and answer-free, session-bound form metadata.

Baseline rows contain only retained-input references, comparison digests and
lifecycle metadata. No field can store an in-progress answer. Submission rows
are complete historical aggregates; mutable selectors and derived proposals
have separate owners and may never rewrite a submitted Family value.
"""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)


class FamilyFormBaseline(MutableRecord):
    """Trusted reviewed inputs, retained independently of the browser's memory."""

    family = models.ForeignKey(
        "stewardship_campaigns.FamilyCampaign", on_delete=models.PROTECT
    )
    # Session UUID is attribution, not the cookie/session key. Keeping it soft
    # allows normal expired-session deletion without deleting response history.
    family_session_id = models.UUIDField()
    mode = models.CharField(max_length=4)
    rehearsal_epoch_id = models.UUIDField(null=True)
    source = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    prior_submission = models.ForeignKey(
        "Submission",
        on_delete=models.PROTECT,
        null=True,
        related_name="later_baselines",
    )
    form_schema = models.CharField(max_length=48)
    projection_version = models.CharField(max_length=48)
    projection_digest = models.CharField(max_length=64)
    definition_digest = models.CharField(max_length=64)
    expires_at = UTCDateTimeField()
    state = models.CharField(max_length=12, default="open")
    ended_at = UTCDateTimeField(null=True)

    immutable_fields = MutableRecord.immutable_fields + (
        "family_id",
        "family_session_id",
        "mode",
        "rehearsal_epoch_id",
        "source_id",
        "configuration_id",
        "prior_submission_id",
        "form_schema",
        "projection_version",
        "projection_digest",
        "definition_digest",
        "expires_at",
    )

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_family_form_baseline"
        indexes = [
            models.Index(fields=["state", "expires_at"], name="family_baseline_expiry"),
            models.Index(
                fields=["family_session_id", "state"], name="family_baseline_session"
            ),
        ]
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(mode="live", rehearsal_epoch_id__isnull=True)
                | models.Q(mode="test", rehearsal_epoch_id__isnull=False),
                name="family_baseline_mode_epoch",
            ),
            models.CheckConstraint(
                condition=models.Q(state="open", ended_at__isnull=True)
                | models.Q(
                    state__in=["submitted", "replaced", "cancelled", "expired"],
                    ended_at__isnull=False,
                    ended_at__gte=models.F("created_at"),
                ),
                name="family_baseline_state_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")),
                name="family_baseline_future_expiry",
            ),
            models.CheckConstraint(
                condition=models.Q(projection_digest__regex=r"^[0-9a-f]{64}$")
                & models.Q(definition_digest__regex=r"^[0-9a-f]{64}$")
                & ~models.Q(form_schema="")
                & ~models.Q(projection_version=""),
                name="family_baseline_contract_identity",
            ),
        ]


class Submission(ImmutableRecord):
    """A complete final answer, never a draft or an Administrator's reviewed edit."""

    family = models.ForeignKey(
        "stewardship_campaigns.FamilyCampaign", on_delete=models.PROTECT
    )
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    campaign_sequence = models.PositiveBigIntegerField()
    baseline = models.OneToOneField(FamilyFormBaseline, on_delete=models.PROTECT)
    reviewed_source = models.ForeignKey(
        "stewardship_source.SourceSnapshot",
        on_delete=models.PROTECT,
        related_name="reviewed_submissions",
    )
    validation_source = models.ForeignKey(
        "stewardship_source.SourceSnapshot",
        on_delete=models.PROTECT,
        related_name="validated_submissions",
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    prior_submission = models.ForeignKey("self", on_delete=models.PROTECT, null=True)
    mode = models.CharField(max_length=4)
    rehearsal_epoch_id = models.UUIDField(null=True)
    family_version = models.PositiveBigIntegerField()
    submitted_at = UTCDateTimeField()
    submitted_on = models.DateField()
    form_schema = models.CharField(max_length=48)
    answers = models.JSONField()
    annual_pledge = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    # The Admin (Staff or Administrator) who entered this response for the
    # Family through Open form (#529), or None when the Family entered it.
    # A soft reference like ``actor_id``: no foreign key, so history outlives
    # the portal user. Added by frozen migration 0065.
    entered_by_id = models.UUIDField(null=True)
    # ParishSoft's pledge for the campaign's upcoming financial period in the
    # validation snapshot, recorded at the submit only for a response that
    # answers the financial questions and only with that snapshot's proven
    # giving read, else None (#1753, frozen migration 0145, provisional). The
    # financial stream's A for the pledge; a guard checks it in SQL.
    upcoming_pledge = models.DecimalField(max_digits=24, decimal_places=2, null=True)

    class Meta:
        db_table = "stewardship_submission"
        indexes = [
            models.Index(
                fields=["family", "mode", "submitted_at"], name="submission_family_time"
            ),
            models.Index(fields=["mode", "submitted_on"], name="submission_local_day"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["campaign", "mode", "campaign_sequence"],
                name="submission_campaign_sequence",
            ),
            models.UniqueConstraint(
                fields=["family", "mode", "family_version"],
                name="submission_family_version",
            ),
            models.CheckConstraint(
                condition=models.Q(mode="live", rehearsal_epoch_id__isnull=True)
                | models.Q(mode="test", rehearsal_epoch_id__isnull=False),
                name="submission_mode_epoch",
            ),
            models.CheckConstraint(
                condition=models.Q(family_version__gt=0, campaign_sequence__gt=0)
                & ~models.Q(form_schema=""),
                name="submission_version_identity",
            ),
            models.CheckConstraint(
                condition=models.Q(annual_pledge__isnull=True)
                | models.Q(annual_pledge__gte=0, annual_pledge__lte="999999999.99"),
                name="submission_supported_pledge",
            ),
            models.CheckConstraint(
                condition=~models.Q(prior_submission_id=models.F("id")),
                name="submission_not_own_predecessor",
            ),
        ]


class ProposedChange(MutableRecord):
    """One atomic Family request with independent decision/execution dimensions."""

    submission = models.ForeignKey(Submission, on_delete=models.PROTECT)
    entity_kind = models.CharField(max_length=20)
    entity_key = models.CharField(max_length=36)
    field = models.CharField(max_length=40)
    baseline_available = models.BooleanField()
    baseline_value = models.JSONField(null=True)
    submitted_value = models.JSONField(null=True)
    current_available = models.BooleanField()
    current_value = models.JSONField(null=True)
    current_source = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT
    )
    admin_value_set = models.BooleanField(default=False)
    admin_value = models.JSONField(null=True)
    handling = models.CharField(max_length=12)
    decision = models.CharField(max_length=12, default="unreviewed")
    execution = models.CharField(max_length=20, default="pending")
    superseded_by = models.ForeignKey("self", on_delete=models.PROTECT, null=True)

    immutable_fields = MutableRecord.immutable_fields + (
        "submission_id",
        "entity_kind",
        "entity_key",
        "field",
        "baseline_available",
        "baseline_value",
        "submitted_value",
        "handling",
    )

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_proposed_change"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["submission", "entity_kind", "entity_key", "field"],
                name="proposal_atomic_identity",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    entity_kind__in=["family", "member", "proposed_member"]
                )
                & ~models.Q(entity_key="")
                & ~models.Q(field=""),
                name="proposal_entity_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(handling__in=["api", "manual", "report-only"]),
                name="proposal_handling",
            ),
            models.CheckConstraint(
                condition=models.Q(decision__in=["unreviewed", "approved", "ignored"]),
                name="proposal_decision",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    execution__in=[
                        "pending",
                        "conflict",
                        "queued",
                        "published",
                        "resolved_upstream",
                        "resolved_external",
                        "failed",
                        "superseded",
                        "cancelled",
                    ]
                ),
                name="proposal_execution",
            ),
            models.CheckConstraint(
                condition=models.Q(baseline_available=True)
                | models.Q(baseline_value__isnull=True),
                name="proposal_baseline_availability",
            ),
            models.CheckConstraint(
                condition=models.Q(current_available=True)
                | models.Q(current_value__isnull=True),
                name="proposal_current_availability",
            ),
            models.CheckConstraint(
                condition=models.Q(admin_value_set=True)
                | models.Q(admin_value__isnull=True),
                name="proposal_admin_value_shape",
            ),
            models.CheckConstraint(
                condition=~models.Q(superseded_by_id=models.F("id")),
                name="proposal_not_own_successor",
            ),
        ]
        indexes = [
            models.Index(fields=["decision", "execution"], name="proposal_review_queue")
        ]


class AdditionalInformationItem(MutableRecord):
    """Live follow-up text with explicit correction/withdrawal rather than deletion."""

    submission = models.OneToOneField(Submission, on_delete=models.PROTECT)
    text = models.TextField()
    disposition = models.CharField(max_length=20, default="current_actionable")
    replacement = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, related_name="replaces"
    )
    follow_up_needed = models.BooleanField(default=False)
    followed_up_at = UTCDateTimeField(null=True)

    immutable_fields = MutableRecord.immutable_fields + ("submission_id", "text")

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_additional_information"
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=~models.Q(text=""), name="additional_nonblank_text"
            ),
            models.CheckConstraint(
                condition=models.Q(disposition="superseded", replacement__isnull=False)
                | models.Q(
                    disposition__in=["current_actionable", "withdrawn"],
                    replacement__isnull=True,
                ),
                name="additional_disposition_shape",
            ),
            models.CheckConstraint(
                condition=~models.Q(replacement_id=models.F("id")),
                name="additional_not_own_replacement",
            ),
        ]
        indexes = [
            models.Index(
                fields=["disposition", "created_at"], name="additional_actionable_queue"
            )
        ]


INFORMATION_DECISIONS = ("accepted", "ignored", "none")


class AdditionalInformationRevision(ImmutableRecord):
    """An authorized Staff edit with immutable notes and completion attribution.

    The item retains current checkbox state; this history owns notes and the
    completing Staff identity. Family-driven disposition changes still advance
    the same item version, invalidating stale Staff forms without erasing notes.
    """

    item = models.ForeignKey(
        AdditionalInformationItem, on_delete=models.PROTECT, related_name="revisions"
    )
    expected_version = models.PositiveBigIntegerField()
    request_key = models.UUIDField()
    follow_up_needed = models.BooleanField()
    followed_up = models.BooleanField()
    confirm_clear = models.BooleanField(default=False)
    notes = models.TextField(default="", blank=True)
    followed_up_at = UTCDateTimeField(null=True)
    followed_up_by_id = models.UUIDField(null=True)
    # The review decision this revision records (#1685, schema migration
    # 0134): ``accepted`` (to handle, or handled once followed up),
    # ``ignored`` or ``none`` (to review). Every revision since 0134 states
    # it; an older one has none, and is read by its follow-up flags
    # (``pipeline.information_placement``).
    review_decision = models.CharField(max_length=12, null=True)

    class Meta:
        db_table = "stewardship_information_revision"
        constraints = [
            models.UniqueConstraint(
                fields=("item", "expected_version"), name="information_revision_version"
            ),
            models.UniqueConstraint(
                fields=("actor_id", "request_key"), name="information_revision_replay"
            ),
            models.CheckConstraint(
                condition=models.Q(expected_version__gte=1, actor_id__isnull=False),
                name="information_revision_identity",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    followed_up=True,
                    followed_up_at__isnull=False,
                    followed_up_by_id__isnull=False,
                )
                | models.Q(
                    followed_up=False,
                    followed_up_at__isnull=True,
                    followed_up_by_id__isnull=True,
                ),
                name="information_revision_completion",
            ),
            # Accepted needs follow-up needed or completed; Ignored and To
            # review (none) need neither.
            models.CheckConstraint(
                condition=models.Q(review_decision__isnull=True)
                | models.Q(review_decision="accepted", follow_up_needed=True)
                | models.Q(review_decision="accepted", followed_up=True)
                | models.Q(
                    review_decision__in=INFORMATION_DECISIONS[1:],
                    follow_up_needed=False,
                    followed_up=False,
                ),
                name="information_revision_decision",
            ),
        ]


LIMITATION_KINDS = ("member", "proposed_member", "family")
LIMITATION_DECISIONS = ("accepted", "ignored", "none", "handled")


class LimitationReview(ImmutableRecord):
    """One review decision on a cannot-participate or cannot-attend answer (#1685).

    Nothing is stored when a Family submits: the answer is the Family's live
    response, as the Limitations report reads it. Each Accept, Ignore,
    Handled or Back to review (``none``) is one immutable row naming the
    answer (``kind`` and, for a Member, ``member_key``: the key the
    response's service answers use), the effective live response it was
    decided on, and ``expected_version``, the number of decisions recorded
    for the answer before it. A decision stays in force while every later
    live response gives the same answer
    (schema/migrations/0134_information_limitation_review.sql).
    """

    family = models.ForeignKey(
        "stewardship_campaigns.FamilyCampaign",
        on_delete=models.PROTECT,
        related_name="+",
    )
    submission = models.ForeignKey(
        Submission, on_delete=models.PROTECT, related_name="+"
    )
    kind = models.CharField(max_length=16)
    member_key = models.CharField(max_length=64, blank=True, default="")
    expected_version = models.PositiveBigIntegerField()
    request_key = models.UUIDField()
    decision = models.CharField(max_length=12)
    note = models.TextField(default="", blank=True)

    class Meta:
        db_table = "stewardship_limitation_review"
        constraints = [
            models.UniqueConstraint(
                fields=("family", "kind", "member_key", "expected_version"),
                name="limitation_review_version",
            ),
            models.UniqueConstraint(
                fields=("actor_id", "request_key"), name="limitation_review_replay"
            ),
            models.CheckConstraint(
                condition=models.Q(actor_id__isnull=False),
                name="limitation_review_identity",
            ),
            models.CheckConstraint(
                condition=models.Q(kind__in=LIMITATION_KINDS),
                name="limitation_review_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(decision__in=LIMITATION_DECISIONS),
                name="limitation_review_decision",
            ),
            # A Family's own answer has no Member key; a Member's has one.
            models.CheckConstraint(
                condition=models.Q(kind="family", member_key="")
                | (~models.Q(kind="family") & ~models.Q(member_key="")),
                name="limitation_review_key",
            ),
        ]


RESOLUTION_ACTIONS = ("entered", "ignored", "reopened")


class ProposalResolution(ImmutableRecord):
    """One Staff or Admin resolution of a census change, kept as history (#528).

    ``entered`` records that a person entered a By-hand change in ParishSoft
    (the proposal's execution becomes ``resolved_external``), ``ignored``
    that it will not be entered (its decision becomes ``ignored``), and
    ``reopened`` an Administrator's undoing of an Ignore (its decision is
    ``unreviewed`` again). Who, when and the optional note live here; the
    proposal carries only the resulting state, which SQL pairs with this row
    (schema/migrations/0060_census_resolution.sql).
    """

    proposal = models.ForeignKey(
        ProposedChange, on_delete=models.PROTECT, related_name="resolutions"
    )
    expected_version = models.PositiveBigIntegerField()
    request_key = models.UUIDField()
    action = models.CharField(max_length=12)
    note = models.TextField(default="", blank=True)

    class Meta:
        db_table = "stewardship_proposal_resolution"
        constraints = [
            models.UniqueConstraint(
                fields=("proposal", "expected_version"),
                name="proposal_resolution_version",
            ),
            models.UniqueConstraint(
                fields=("actor_id", "request_key"), name="proposal_resolution_replay"
            ),
            models.CheckConstraint(
                condition=models.Q(actor_id__isnull=False, expected_version__gte=1),
                name="proposal_resolution_identity",
            ),
            models.CheckConstraint(
                condition=models.Q(action__in=RESOLUTION_ACTIONS),
                name="proposal_resolution_action",
            ),
        ]


REVIEW_DECISIONS = ("approved", "ignored", "unreviewed")


class ProposalReview(ImmutableRecord):
    """One Administrator's review of an Automatic census change (#528, ADM-09).

    An Automatic change (an API-writable Member field) is published only once
    an Administrator approves it. Each Approve, Ignore or Back to To review is
    one immutable row: who, when, the optional note, and the review state the
    proposal carries afterwards (its ``decision`` and edit fields), which SQL
    pairs with the proposal (schema/migrations/0096_proposal_review.sql). The
    edit fields record the whole resulting state. A row is either a decision
    (the edit fields stay the proposal's own) or, since 0107, an Edit value
    or Clear edit (ADM-09.04: the decision stays and the edit fields change).

    Since 0108 a row may instead resolve a Conflict (ADM-09.03, "Use the
    Family's value anyway"): ``accepted_base`` is set, the decision becomes
    approved, and ``accepted_value``/``accepted_source_id`` record the
    ParishSoft value the Administrator accepted and the snapshot it came
    from. The base is active while that snapshot is still the proposal's
    ``current_source_id`` (``accepted_base.with_accepted_base``); publication
    then merges against it rather than the Family's baseline.
    """

    proposal = models.ForeignKey(
        ProposedChange, on_delete=models.PROTECT, related_name="reviews"
    )
    expected_version = models.PositiveBigIntegerField()
    request_key = models.UUIDField()
    decision = models.CharField(max_length=12)
    admin_value_set = models.BooleanField(default=False)
    admin_value = models.JSONField(null=True)
    note = models.TextField(default="", blank=True)
    # A conflict resolution's accepted ParishSoft value (0108). No foreign
    # key on the snapshot: source retention must stay free to drop it.
    accepted_base = models.BooleanField(default=False, db_default=False)
    accepted_value = models.JSONField(null=True)
    accepted_source_id = models.UUIDField(null=True)

    class Meta:
        db_table = "stewardship_proposal_review"
        constraints = [
            models.UniqueConstraint(
                fields=("proposal", "expected_version"),
                name="proposal_review_version",
            ),
            models.UniqueConstraint(
                fields=("actor_id", "request_key"), name="proposal_review_replay"
            ),
            models.CheckConstraint(
                condition=models.Q(actor_id__isnull=False, expected_version__gte=1),
                name="proposal_review_identity",
            ),
            models.CheckConstraint(
                condition=models.Q(decision__in=REVIEW_DECISIONS),
                name="proposal_review_decision",
            ),
            models.CheckConstraint(
                condition=models.Q(admin_value_set=True)
                | models.Q(admin_value__isnull=True),
                name="proposal_review_admin_value_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    accepted_base=False,
                    accepted_value__isnull=True,
                    accepted_source_id__isnull=True,
                )
                | models.Q(
                    accepted_base=True,
                    decision="approved",
                    accepted_source_id__isnull=False,
                ),
                name="proposal_review_accepted_shape",
            ),
        ]


class SubmissionReceiptOccurrence(ImmutableRecord):
    """One atomic receipt or proven no-recipient outcome per accepted submission.

    Preparation is a write-only command input consumed by the database trigger;
    the retained row points to the outbox, never to answers or credentials.
    """

    submission = models.OneToOneField(Submission, on_delete=models.PROTECT)
    disposition = models.CharField(max_length=28)
    outbox = models.OneToOneField(
        "stewardship_jobs.OutboxMessage", null=True, on_delete=models.PROTECT
    )
    preparation = models.JSONField(null=True)

    class Meta:
        db_table = "stewardship_submission_receipt"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(disposition="queued", outbox__isnull=False)
                | models.Q(disposition="no_deliverable_recipient", outbox__isnull=True),
                name="submission_receipt_disposition",
            ),
            models.CheckConstraint(
                condition=models.Q(preparation__isnull=True),
                name="submission_receipt_scrubbed",
            ),
        ]


# Publication plans and outcomes (#528, DAT-09.01) live in their own module.
from .publication_models import (  # noqa: E402,F401
    PublicationConfirmation,
    PublicationEntity,
    PublicationField,
    PublicationOutcome,
    PublicationPlan,
)
