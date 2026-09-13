"""Repeated live responses and real promotion preserve Family intent and history."""

from dataclasses import replace

import pytest

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.responses.baselines import (
    effective_submission,
    issue_baseline,
)
from parishkit.stewardship.responses.effective import effective_fields
from parishkit.stewardship.responses.models import (
    AdditionalInformationItem,
    ProposedChange,
    Submission,
)
from parishkit.stewardship.source.models import SourceSnapshotPin

from .response_builders import response_source
from .test_family_auth_postgresql import login
from .test_response_submission_postgresql import form_and_answers, submit
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


def test_response_reconciliation_uses_exact_restricted_worker(live_response_service):
    """Existing pending responses must not force broad worker answer-read authority."""
    from parishkit.stewardship.deployment import ServiceRole

    from .test_background_grants_postgresql import task_login

    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    answers["members"]["3"]["first_name"] = "Family update"
    first = submit(harness, form, answers).submission
    data = response_source()
    data.members[3]["firstName"] = "Source update"
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    proposal = ProposedChange.objects.get(submission=first)
    assert (
        proposal.execution == "conflict" and proposal.current_source_id == snapshot.pk
    )
    assert SourceSnapshotPin.objects.filter(
        parent_kind="submission", parent_id=first.pk, snapshot_id=snapshot.pk
    ).exists()


def revisit(harness):
    """Reauthenticate and use only merged public values, never hidden source data."""
    client, response = login(harness.code)
    assert response.status_code == 302
    harness = replace(harness, client=client, request=response.wsgi_request)
    form = issue_baseline(harness.request, harness.service)
    with work_transaction():
        prior = effective_submission(
            harness.request.family_session.family, harness.request.family_session
        )
        fields = effective_fields(form.inputs, prior)
    members = {}
    for field in fields:
        if field.entity == "member":
            members.setdefault(str(field.identity), {})[field.field] = (
                field.effective.value.value or ""
            )
    return (
        harness,
        form,
        {
            "members": members,
            "additional_information": prior.answers["additional_information"]
            if prior
            else "",
            "testing_acknowledged": False,
        },
        fields,
    )


def test_repeat_response_keeps_pending_value_and_first_participation(
    live_response_service,
):
    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    answers["members"]["3"]["first_name"] = "Family update"
    first = submit(harness, form, answers).submission
    harness, form, answers, fields = revisit(harness)
    assert answers["members"]["3"]["first_name"] == "Family update"
    assert next(
        field for field in fields if field.field == "first_name"
    ).effective.changed
    second = submit(harness, form, answers).submission
    second.family.refresh_from_db()
    assert second.family.first_live_submission_id == first.pk
    assert second.family.effective_submission_id == second.pk
    assert second.prior_submission_id == first.pk
    assert second.family_version == second.campaign_sequence == 2
    old = ProposedChange.objects.get(submission=first)
    new = ProposedChange.objects.get(submission=second)
    assert old.execution == "superseded" and old.superseded_by_id == new.pk
    assert new.baseline_value == "Member" and new.submitted_value == "Family update"
    first.refresh_from_db()
    assert first.answers["members"]["3"]["first_name"] == "Family update"


def test_additional_text_replacement_and_withdrawal_keep_history(live_response_service):
    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    answers["additional_information"] = "First request"
    submit(harness, form, answers)
    old = AdditionalInformationItem.objects.get()
    harness, form, answers, _ = revisit(harness)
    submit(harness, form, answers)
    assert AdditionalInformationItem.objects.count() == 1
    harness, form, answers, _ = revisit(harness)
    answers["additional_information"] = "Replacement request"
    submit(harness, form, answers)
    old.refresh_from_db()
    replacement = AdditionalInformationItem.objects.get(
        disposition="current_actionable"
    )
    assert old.disposition == "superseded" and old.replacement_id == replacement.pk
    harness, form, answers, _ = revisit(harness)
    answers["additional_information"] = ""
    submit(harness, form, answers)
    replacement.refresh_from_db()
    assert replacement.disposition == "withdrawn"
    assert AdditionalInformationItem.objects.count() == 2
    assert not AdditionalInformationItem.objects.filter(
        disposition="current_actionable"
    ).exists()


@pytest.mark.parametrize(
    "source_name,execution,display,conflict",
    [
        ("Family update", "resolved_upstream", "Family update", False),
        ("Source update", "conflict", "Family update", True),
        ("Member", "pending", "Family update", False),
    ],
)
def test_promotion_reconciles_same_transaction_and_revisit_uses_merged_value(
    live_response_service, source_name, execution, display, conflict
):
    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    answers["members"]["3"]["first_name"] = "Family update"
    first = submit(harness, form, answers).submission
    proposal = ProposedChange.objects.get(submission=first)
    original_version, original_source = proposal.version, proposal.current_source_id
    data = response_source()
    data.members[3]["firstName"] = source_name
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    proposal.refresh_from_db()
    assert proposal.execution == execution
    if source_name == "Member":
        assert (
            proposal.current_source_id == original_source
            and proposal.version == original_version
        )
        assert not SourceSnapshotPin.objects.filter(
            parent_kind="submission", parent_id=first.pk, snapshot_id=snapshot.pk
        ).exists()
    else:
        assert (
            proposal.current_source_id == snapshot.pk
            and proposal.version == original_version + 1
        )
        assert SourceSnapshotPin.objects.filter(
            parent_kind="submission", parent_id=first.pk, snapshot_id=snapshot.pk
        ).exists()
    harness, form, answers, fields = revisit(harness)
    effective = next(field for field in fields if field.field == "first_name").effective
    assert effective.value.value == display and effective.conflict is conflict
    assert effective.changed is (execution != "resolved_upstream")
    assert Submission.objects.count() == 1
    first.refresh_from_db()
    assert first.answers["members"]["3"]["first_name"] == "Family update"
