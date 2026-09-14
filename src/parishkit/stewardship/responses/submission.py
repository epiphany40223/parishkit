"""One local final-submit transaction, with no provider or queue I/O inside it."""

from dataclasses import dataclass
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.db.models import F, Max

from parishkit.stewardship.accounts.sessions import database_now, revoke_family_sessions
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.models import CampaignControlChange
from parishkit.stewardship.campaigns.runtime import _now
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.reports.demand import request_rebuild
from parishkit.stewardship.reports.inputs import FactInputs
from parishkit.stewardship.source.pins import pin_snapshot

from .answers import validate_answers
from .baselines import (
    FormBaseline,
    _pin_admission,
    admitted_family,
    cancel_session_baselines,
    end_baseline,
    issue_baseline,
)
from .followup import derive_additional_information
from .inputs import FORM_SCHEMA
from .ministry_requests import derive_ministry_requests
from .models import Submission, SubmissionReceiptOccurrence
from .proposals import derive_proposals
from .validation import validate_baseline


@dataclass(frozen=True)
class SubmissionResult:
    """Either an accepted immutable response or fresh metadata requiring review."""

    submission: Submission | None = None
    refreshed: FormBaseline | None = None


def _live_effects(submission, *, campaign, configuration, family):
    """Advance live-only pointers and request derived facts in the same commit."""
    type(family).objects.filter(pk=family.pk).update(
        first_live_submission_id=family.first_live_submission_id or submission.pk,
        effective_submission_id=submission.pk,
        version=F("version") + 1,
    )
    if campaign.first_live_submission_at is None:
        # The existing control ledger owns this write-once campaign marker.
        # Its public wrapper owns a top-level transaction; this composing owner
        # has already taken its same admission locks and verified the evidence.
        CampaignControlChange.objects.create(
            campaign=campaign,
            request_id=uuid4(),
            action="first_submission",
            expected_version=campaign.version,
            expected_runtime_version=configuration.version,
            actor_id=family.pk,
            evidence_id=submission.pk,
            occurred_at=submission.submitted_at,
        )
    for population in ("historical", "current"):
        request_rebuild(
            FactInputs(
                campaign.pk,
                population,
                submission.validation_source_id,
                submission.campaign_sequence,
                campaign.active_configuration_id,
                submission.submitted_on,
            ),
            admit=lambda action, inputs: (
                action == "request" and inputs.campaign_id == campaign.pk
            ),
        )


def submit_family(request, service, *, baseline_id, payload):
    """Check current admission/relevant versions, then commit all final effects.

    Stale input returns a new answer-free baseline, not an auto-rebased response.
    The caller keeps unsaved edits only in its existing tab memory. Exceptions
    roll back the response, pointers, proposals, follow-up, audit, pins, receipt
    intent and fact demand together. Replays cannot reuse a submitted baseline.
    """
    with work_transaction():
        configuration, campaign, family, session = admitted_family(request, service)
        validated = validate_baseline(
            baseline_id, family=family, session=session, campaign=campaign
        )
        if validated.review_required:
            return SubmissionResult(refreshed=issue_baseline(request, service))
        answers = validate_answers(
            payload,
            validated.current,
            additional_enabled=campaign.active_configuration.values[
                "additional_information"
            ],
            testing=session.mode == "testing",
            today=_now()
            .astimezone(ZoneInfo(campaign.active_configuration.timezone))
            .date(),
        )
        mode = "test" if session.mode == "testing" else "live"
        sequence = (
            Submission.objects.filter(campaign=campaign, mode=mode).aggregate(
                last=Max("campaign_sequence")
            )["last"]
            or 0
        )
        prior = validated.prior_submission
        # Prior answers are epoch-scoped; uniqueness is Family/mode-scoped.
        # Invalidated rehearsal rows may still await bounded asynchronous cleanup.
        family_version = (
            Submission.objects.filter(family=family, mode=mode).aggregate(
                last=Max("family_version")
            )["last"]
            or 0
        )
        now = _now()
        submission = Submission.objects.create(
            family=family,
            campaign=campaign,
            campaign_sequence=sequence + 1,
            baseline=validated.baseline,
            reviewed_source_id=validated.baseline.source_id,
            validation_source_id=validated.validation_snapshot_id,
            configuration=configuration.active_configuration,
            prior_submission=prior,
            mode=mode,
            rehearsal_epoch_id=session.rehearsal_epoch_id,
            family_version=family_version + 1,
            submitted_at=now,
            submitted_on=now.astimezone(
                ZoneInfo(campaign.active_configuration.timezone)
            ).date(),
            form_schema=FORM_SCHEMA,
            answers=answers,
            annual_pledge=None,
            actor_id=family.pk,
        )
        submission.refresh_from_db(fields=["submitted_at", "submitted_on"])
        for source_id in sorted(
            {submission.reviewed_source_id, submission.validation_source_id}
        ):
            pin_snapshot(
                source_id,
                parent_kind="submission",
                parent_id=submission.pk,
                admit=_pin_admission,
            )
        derive_proposals(submission, validated)
        derive_ministry_requests(submission, validated)
        derive_additional_information(submission, prior)
        receipt = SubmissionReceiptOccurrence.objects.create(
            submission=submission,
            disposition="pending_preparation"
            if family.email_deliverable
            else "no_deliverable_recipient",
        )
        AuditEvent.objects.create(
            event_type="family_submission"
            if mode == "live"
            else "family_test_submission",
            subject_id=submission.pk if mode == "live" else None,
            actor_id=family.pk if mode == "live" else None,
        )
        if receipt.disposition == "no_deliverable_recipient":
            AuditEvent.objects.create(
                event_type="submission_receipt_skipped",
                subject_id=receipt.pk if mode == "live" else None,
            )
        if mode == "live":
            _live_effects(
                submission,
                campaign=campaign,
                configuration=configuration,
                family=family,
            )
        end_baseline(validated.baseline, state="submitted")
        cancel_session_baselines([session.pk])
        revoke_family_sessions([session], now=database_now())
        return SubmissionResult(submission=submission)
