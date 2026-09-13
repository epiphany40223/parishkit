"""Final Submit commits one complete response and all local effects, or nothing."""

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import FamilySession
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.reports.models import CampaignFactRebuildDemand
from parishkit.stewardship.responses.answers import InvalidAnswers
from parishkit.stewardship.responses.baselines import (
    FamilyAdmissionDenied,
    issue_baseline,
)
from parishkit.stewardship.responses.models import (
    AdditionalInformationItem,
    ProposedChange,
    Submission,
    SubmissionReceiptOccurrence,
)
from parishkit.stewardship.responses.submission import submit_family
from parishkit.stewardship.source.models import SourceSnapshotPin

from .response_builders import response_source
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def form_and_answers(harness):
    """Use the actual authorized field projection as a browser would receive it."""
    form = issue_baseline(harness.request, harness.service, testing_acknowledged=True)
    members = {}
    for field in form.inputs.fields:
        if field.entity == "member":
            members.setdefault(str(field.identity), {})[field.field] = (
                field.source.value or ""
            )
    return form, {
        "members": members,
        "additional_information": "",
        "testing_acknowledged": True,
    }


def submit(harness, form, answers):
    """Exercise the final owner without bypassing its session or source checks."""
    return submit_family(
        harness.request, harness.service, baseline_id=form.baseline.pk, payload=answers
    )


def test_no_change_test_submission_is_complete_and_isolated(response_service):
    form, answers = form_and_answers(response_service)
    result = submit(response_service, form, answers)
    row = result.submission
    assert row is not None and result.refreshed is None
    assert row.mode == "test" and row.family_version == row.campaign_sequence == 1
    assert row.answers["members"] == answers["members"]
    assert (
        row.reviewed_source_id
        == row.validation_source_id
        == response_service.snapshot.pk
    )
    assert row.submitted_on == response_service.campaign.active_configuration.start_date
    assert not ProposedChange.objects.exists()
    assert not AdditionalInformationItem.objects.exists()
    assert not CampaignFactRebuildDemand.objects.exists()
    assert (
        row.family.first_live_submission_id is None
        and row.family.effective_submission_id is None
    )
    assert (
        SubmissionReceiptOccurrence.objects.get(submission=row).disposition
        == "pending_preparation"
    )
    assert (
        FamilySession.objects.get(pk=form.baseline.family_session_id).revoked_at
        is not None
    )
    form.baseline.refresh_from_db()
    assert form.baseline.state == "submitted"
    assert not SourceSnapshotPin.objects.filter(
        parent_kind="form_baseline", parent_id=form.baseline.pk
    ).exists()
    assert SourceSnapshotPin.objects.filter(
        parent_kind="submission", parent_id=row.pk, snapshot_id=row.reviewed_source_id
    ).exists()
    event = AuditEvent.objects.get(event_type="family_test_submission")
    assert event.actor_id is None and event.subject_id is None


def test_changed_response_derives_atomic_field_proposal_not_staff_work_in_test(
    response_service,
):
    form, answers = form_and_answers(response_service)
    answers["members"]["3"]["first_name"] = "Changed"
    answers["additional_information"] = "Disposable test text"
    row = submit(response_service, form, answers).submission
    proposal = ProposedChange.objects.get()
    assert proposal.submission_id == row.pk
    assert proposal.field == "first_name" and proposal.entity_key == "3"
    assert proposal.baseline_value == "Member" and proposal.submitted_value == "Changed"
    assert proposal.current_value == "Member" and proposal.handling == "api"
    assert proposal.decision == "unreviewed" and proposal.execution == "pending"
    assert not AdditionalInformationItem.objects.exists()


def test_final_testing_ack_is_separate_from_entry_ack(response_service):
    form, answers = form_and_answers(response_service)
    answers["testing_acknowledged"] = False
    with pytest.raises(InvalidAnswers):
        submit(response_service, form, answers)
    assert not Submission.objects.exists()
    assert (
        FamilySession.objects.get(pk=form.baseline.family_session_id).revoked_at is None
    )


def test_failure_after_inserting_response_and_pins_rolls_back_and_can_retry(
    response_service, monkeypatch
):
    from parishkit.stewardship.responses import submission

    form, answers = form_and_answers(response_service)

    def fail(*args):
        """Simulate a local derived owner failing after history/pins were inserted."""
        assert Submission.objects.count() == 1
        raise RuntimeError("Synthetic downstream failure")

    with monkeypatch.context() as patch:
        patch.setattr(submission, "derive_proposals", fail)
        with pytest.raises(RuntimeError, match="Synthetic downstream"):
            submit(response_service, form, answers)
    assert not Submission.objects.exists()
    assert not SubmissionReceiptOccurrence.objects.exists()
    assert not SourceSnapshotPin.objects.filter(parent_kind="submission").exists()
    form.baseline.refresh_from_db()
    assert form.baseline.state == "open"
    assert submit(response_service, form, answers).submission is not None


def test_relevant_refresh_returns_new_metadata_without_saving_answers(response_service):
    form, answers = form_and_answers(response_service)
    answers["members"]["3"]["first_name"] = "Unsaved"
    data = response_source()
    data.members[3]["firstName"] = "Updated source"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, response_service.campaign, response_service.rings)
    result = submit(response_service, form, answers)
    assert result.submission is None and result.refreshed is not None
    assert not Submission.objects.exists() and not ProposedChange.objects.exists()
    assert result.refreshed.baseline.source_id == snapshot.pk
    form.baseline.refresh_from_db()
    assert form.baseline.state == "replaced"
    assert (
        FamilySession.objects.get(pk=form.baseline.family_session_id).revoked_at is None
    )


def test_double_submit_cannot_reuse_revoked_session_or_baseline(response_service):
    form, answers = form_and_answers(response_service)
    submit(response_service, form, answers)
    with pytest.raises(FamilyAdmissionDenied):
        submit(response_service, form, answers)
    assert (
        Submission.objects.count() == SubmissionReceiptOccurrence.objects.count() == 1
    )


def test_raw_sql_cannot_rewrite_submission_history(response_service):
    form, answers = form_and_answers(response_service)
    row = submit(response_service, form, answers).submission
    with (
        pytest.raises(IntegrityError, match="immutable"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_submission SET answers='{}'::jsonb WHERE id=%s",
            [row.pk],
        )
    with (
        pytest.raises(IntegrityError, match="immutable"),
        work_transaction(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM stewardship_submission WHERE id=%s", [row.pk])


def test_final_submit_runs_with_restricted_web_grants(response_service):
    from .test_runtime_auth_grants_postgresql import web_login

    with web_login():
        form, answers = form_and_answers(response_service)
        answers["members"]["3"]["first_name"] = "Updated"
        assert submit(response_service, form, answers).submission is not None


@pytest.mark.parametrize("restricted", [False, True])
def test_live_submission_commits_selectors_followup_receipt_and_fact_demand(
    live_response_service, restricted
):
    from contextlib import nullcontext

    from .test_runtime_auth_grants_postgresql import web_login

    harness = live_response_service
    with web_login() if restricted else nullcontext():
        form, answers = form_and_answers(harness)
        answers["testing_acknowledged"] = False
        answers["additional_information"] = "Please call this household."
        row = submit(harness, form, answers).submission
        assert row.mode == "live"
        row.family.refresh_from_db()
        assert (
            row.family.first_live_submission_id
            == row.family.effective_submission_id
            == row.pk
        )
        assert (
            AdditionalInformationItem.objects.get(submission=row).text
            == answers["additional_information"]
        )
        assert set(
            CampaignFactRebuildDemand.objects.values_list(
                "population_scope", "requested_submission_watermark"
            )
        ) == {("historical", 1), ("current", 1)}
        assert (
            AuditEvent.objects.get(event_type="family_submission").subject_id == row.pk
        )
