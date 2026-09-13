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


class SubmissionReceiptOccurrence(ImmutableRecord):
    """Idempotent confirmation intent; Phase 4 owns preparation and real dispatch.

    This milestone's stub records no provider attempt and claims no delivery.
    It stores the submission identity only; private answers, credentials and
    template substitutions are not copied into an outgoing payload here.
    """

    submission = models.OneToOneField(Submission, on_delete=models.PROTECT)
    disposition = models.CharField(max_length=28)

    class Meta:
        db_table = "stewardship_submission_receipt"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    disposition__in=["pending_preparation", "no_deliverable_recipient"]
                ),
                name="submission_receipt_disposition",
            ),
        ]
