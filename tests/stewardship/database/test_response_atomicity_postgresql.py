"""Cross-domain final-response ordering, history integrity and all-or-nothing work."""

from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import IntegrityError
from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.responses.models import (
    AdditionalInformationItem,
    ProposedChange,
    Submission,
    SubmissionReceiptOccurrence,
)
from parishkit.stewardship.source.models import SourceSnapshotPin

from ..census_factory import address, member
from .test_family_auth_postgresql import login
from .test_response_revisit_postgresql import revisit
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("restricted", [False, True])
@pytest.mark.parametrize("repeated", [False, True])
def test_partial_followup_replacement_cannot_forge_history(
    live_response_service, monkeypatch, restricted, repeated
):
    """A correct latest text cannot hide a false withdrawal or repeated-text item."""
    from contextlib import nullcontext

    from parishkit.stewardship.responses import submission

    from .test_runtime_auth_grants_postgresql import web_login

    harness = live_response_service
    with web_login() if restricted else nullcontext():
        form, answers = form_and_answers(harness)
        answers.update(testing_acknowledged=False, additional_information="First text")
        first = submit(harness, form, answers).submission
        old = AdditionalInformationItem.objects.get(submission=first)
        harness, form, answers, _ = revisit(harness)
        if not repeated:
            answers["additional_information"] = "Replacement text"

        def false_withdrawal(response, prior):
            """Leave the queue correct but intentionally break its history chain."""
            AdditionalInformationItem.objects.filter(pk=old.pk).update(
                disposition="withdrawn", replacement_id=None, version=F("version") + 1
            )
            return AdditionalInformationItem.objects.create(
                submission=response,
                text=response.answers["additional_information"],
                actor_id=response.family_id,
            )

        with monkeypatch.context() as patch:
            patch.setattr(submission, "derive_additional_information", false_withdrawal)
            with pytest.raises(
                IntegrityError, match="follow-up history|linked history"
            ):
                submit(harness, form, answers)
        assert (
            Submission.objects.count() == AdditionalInformationItem.objects.count() == 1
        )
        old.refresh_from_db()
        assert old.disposition == "current_actionable" and old.replacement_id is None
        form.baseline.refresh_from_db()
        assert form.baseline.state == "open"
        result = submit(harness, form, answers).submission
        assert result is not None
        old.refresh_from_db()
        if repeated:
            assert old.disposition == "current_actionable"
            assert AdditionalInformationItem.objects.count() == 1
        else:
            assert old.disposition == "superseded"
            assert old.replacement.submission_id == result.pk


@pytest.mark.parametrize("restricted", [False, True])
@pytest.mark.parametrize(
    "kind",
    [
        "member",
        "household",
        "terminal",
        "proposed",
        "census_withdrawal",
        "followup",
        "followup_replacement",
        "followup_withdrawal",
    ],
)
def test_noop_derivation_owner_cannot_commit_incomplete_response(
    live_response_service, monkeypatch, kind, restricted
):
    """Deferred SQL rejects missing effects when a broken owner returns normally."""
    from contextlib import nullcontext

    from parishkit.stewardship.campaigns.credential_models import FamilySession
    from parishkit.stewardship.responses import submission

    from .test_runtime_auth_grants_postgresql import web_login

    harness = live_response_service
    with web_login() if restricted else nullcontext():
        form, answers = form_and_answers(harness)
        answers["testing_acknowledged"] = False
        if kind in {"census_withdrawal", "followup_replacement", "followup_withdrawal"}:
            answers["members"]["3"]["first_name"] = "Prior request"
            answers["additional_information"] = "Prior follow-up"
            submit(harness, form, answers)
            harness, form, answers, _ = revisit(harness)
        if kind == "member":
            answers["members"]["3"]["first_name"] = "New name"
        elif kind == "household":
            answers["family"]["home_address"] = address()
        elif kind == "terminal":
            answers["members"]["3"] = {"moved_household": True, "confirmed": True}
        elif kind == "proposed":
            answers["proposed_members"][str(uuid4())] = member()
        elif kind == "census_withdrawal":
            answers["members"]["3"]["first_name"] = "Member"
        else:
            answers["additional_information"] = (
                "" if kind == "followup_withdrawal" else "New follow-up"
            )
        models = (
            Submission,
            ProposedChange,
            AdditionalInformationItem,
            SubmissionReceiptOccurrence,
            SourceSnapshotPin,
        )
        before = [list(model.objects.order_by("pk").values()) for model in models]
        owner = (
            "derive_additional_information"
            if kind.startswith("followup")
            else "derive_proposals"
        )
        with monkeypatch.context() as patch:
            patch.setattr(submission, owner, lambda *args: None)
            with pytest.raises(IntegrityError, match="atomic|earlier census work"):
                submit(harness, form, answers)
        assert [
            list(model.objects.order_by("pk").values()) for model in models
        ] == before
        form.baseline.refresh_from_db()
        assert form.baseline.state == "open"
        assert (
            FamilySession.objects.get(pk=form.baseline.family_session_id).revoked_at
            is None
        )
        assert submit(harness, form, answers).submission is not None


def test_two_separate_family_sessions_require_fresh_review_after_first_submit(
    live_response_service,
):
    """A still-authorized second adult cannot silently overwrite the first response."""
    first = live_response_service
    form1, answers1 = form_and_answers(first)
    answers1["testing_acknowledged"] = False
    client, response = login(first.code)
    second = replace(first, client=client, request=response.wsgi_request)
    form2, answers2 = form_and_answers(second)
    answers2["testing_acknowledged"] = False
    answers1["members"]["3"]["first_name"] = "First adult change"
    row1 = submit(first, form1, answers1).submission
    stale = submit(second, form2, answers2)
    assert stale.submission is None and stale.refreshed is not None
    assert stale.refreshed.baseline.prior_submission_id == row1.pk
    assert Submission.objects.count() == 1
    # The next deliberate submission uses the refreshed opaque baseline and
    # explicitly reviewed values, never an automatic server rebase.
    answers2["members"]["3"]["first_name"] = "Second reviewed change"
    row2 = submit(second, stale.refreshed, answers2).submission
    assert row2.prior_submission_id == row1.pk
    assert row2.family_version == 2


@pytest.mark.parametrize("stage", ["followup", "receipt", "facts", "logout"])
def test_late_owner_failure_rolls_back_every_effect(
    live_response_service, monkeypatch, stage
):
    """No failed final transaction creates participation, mail or lost session state."""
    from parishkit.stewardship.campaigns.credential_models import FamilySession
    from parishkit.stewardship.reports.models import CampaignFactRebuildDemand
    from parishkit.stewardship.responses import submission

    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    answers["members"]["3"]["first_name"] = "New name"
    answers["additional_information"] = "Please follow up"

    def fail(*args, **kwargs):
        """Raise only after earlier real owners have exercised their writes."""
        assert Submission.objects.count() == ProposedChange.objects.count() == 1
        raise RuntimeError("Synthetic late owner failure")

    with monkeypatch.context() as patch:
        if stage == "receipt":
            patch.setattr(SubmissionReceiptOccurrence.objects, "create", fail)
        else:
            patch.setattr(
                submission,
                {
                    "followup": "derive_additional_information",
                    "facts": "request_rebuild",
                    "logout": "revoke_family_sessions",
                }[stage],
                fail,
            )
        with pytest.raises(RuntimeError, match="late owner"):
            submit(harness, form, answers)
    assert not Submission.objects.exists()
    assert not ProposedChange.objects.exists()
    assert not AdditionalInformationItem.objects.exists()
    assert not SubmissionReceiptOccurrence.objects.exists()
    assert not CampaignFactRebuildDemand.objects.exists()
    assert not SourceSnapshotPin.objects.filter(parent_kind="submission").exists()
    form.baseline.refresh_from_db()
    assert form.baseline.state == "open"
    assert (
        FamilySession.objects.get(pk=form.baseline.family_session_id).revoked_at is None
    )
    assert submit(harness, form, answers).submission is not None


def test_proposal_cannot_link_backwards_in_response_history(live_response_service):
    """SQL independently enforces same-field, forward-only supersession identity."""
    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    answers["members"]["3"]["first_name"] = "Requested name"
    old = submit(harness, form, answers).submission
    harness, form, answers, _ = revisit(harness)
    new = submit(harness, form, answers).submission
    old_proposal = ProposedChange.objects.get(submission=old)
    new_proposal = ProposedChange.objects.get(submission=new)
    with (
        pytest.raises(IntegrityError, match="incompatible provenance"),
        work_transaction(),
    ):
        ProposedChange.objects.filter(pk=new_proposal.pk).update(
            execution="superseded",
            superseded_by_id=old_proposal.pk,
            version=F("version") + 1,
        )
    new_proposal.refresh_from_db()
    assert new_proposal.execution == "pending"
