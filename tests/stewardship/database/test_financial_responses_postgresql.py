"""Financial intent through real source, authenticated HTTP and restricted SQL."""

from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
from uuid import UUID

import pytest
from django.db import IntegrityError, transaction

from parishkit.stewardship.responses import submission
from parishkit.stewardship.responses.models import (
    ProposedChange,
    Submission,
    SubmissionReceiptOccurrence,
)
from parishkit.stewardship.source.models import SourceSnapshotPin

from ..test_financial_answers import CHECK, OPTIONS, OTHER
from .response_builders import activate_response_service
from .test_family_auth_postgresql import login
from .test_financial_source_postgresql import financial_source
from .test_ministry_responses_postgresql import respond, revisit
from .test_response_http_postgresql import answers_for, load_form, post
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize(
    "modules",
    [
        ["financial"],
        ["census", "financial"],
        ["ministry", "financial"],
        ["census", "ministry", "financial"],
    ],
)
def test_financial_final_submit_revisit_and_replacement(
    response_service, live, modules
):
    """Every enabled combination saves exact annual totals, never source writes."""
    harness = response_service
    financial_source(harness, modules=modules, options=map(asdict, OPTIONS))
    if live:
        harness = activate_response_service(harness)
    with web_login():
        form = load_form(harness)
        financial = form["financial"]
        assert financial["pledge"]["amount"] == "1200.00"
        assert financial["contributions"]["amount"] == "100.00"
        assert financial["answers"]["annual_pledge"] == ""
        assert "9999.00" not in str(financial) and "8888.00" not in str(financial)
        answers = answers_for(form)
        answers["financial"] = {
            "annual_pledge": "1,234.5",
            "frequency": "monthly",
            "shares": {CHECK: "", OTHER: " Cafe\u0301 gift "},
        }
        first = respond(harness, form, answers)
        assert first.annual_pledge == Decimal("1234.50")
        assert first.answers["financial"] == {
            "annual_pledge": "1234.50",
            "frequency": "monthly",
            "shares": {CHECK: "", OTHER: "Café gift"},
        }
        assert not ProposedChange.objects.exists()
        assert SubmissionReceiptOccurrence.objects.filter(submission=first).count() == 1
        form = revisit(harness)
        assert form["financial"]["answers"] == first.answers["financial"]
        assert form["financial"]["previously_submitted"]
        assert bool(form["last_submitted_at"]) is live
        answers = answers_for(form)
        answers["financial"] = {"annual_pledge": "0", "frequency": "", "shares": {}}
        second = respond(harness, form, answers)
        assert second.annual_pledge == Decimal("0.00")
        first.refresh_from_db()
        assert first.annual_pledge == Decimal("1234.50")
        assert second.family_version == 2
        second.family.refresh_from_db()
        assert second.family.effective_submission_id == (second.pk if live else None)


def test_incomplete_source_can_accept_intent_but_never_displays_zero(response_service):
    """A new pledge is independent of unavailable historical giving information."""
    financial_source(response_service, covered=False)
    with web_login():
        form = load_form(response_service)
        assert form["financial"]["pledge"] == {
            "available": False,
            "amount": None,
            "display": "Unavailable",
        }
        answers = answers_for(form)
        answers["financial"] = {"annual_pledge": "0", "frequency": "", "shares": {}}
        assert respond(response_service, form, answers).annual_pledge == 0


def test_financial_source_refresh_requires_explicit_new_submit(response_service):
    """A changed giving observation invalidates a form without saving its answers."""
    harness = response_service
    financial_source(harness)
    form = load_form(harness)
    answers = answers_for(form)
    answers["financial"] = {"annual_pledge": "75", "frequency": "weekly", "shares": {}}
    financial_source(harness, empty=True)
    with web_login():
        response = post(
            harness.client,
            "/family/submit",
            {"baseline": form["baseline"], "answers": answers},
        )
        assert response.status_code == 409, response.content
        fresh = response.json()["form"]
        assert fresh["financial"]["pledge"]["amount"] == "0.00"
        assert fresh["financial"]["answers"]["annual_pledge"] == ""
        assert not Submission.objects.exists()
        assert respond(harness, fresh, answers).annual_pledge == Decimal("75.00")


@pytest.mark.parametrize("fault", ["frequency", "option", "missing", "extra"])
def test_financial_insert_guard_independent_of_python(
    response_service, monkeypatch, fault
):
    """Bypassing only answer validation still cannot commit an invalid financial row."""
    harness = response_service
    financial_source(harness)
    form = load_form(harness)
    answers = answers_for(form)
    answers["financial"] = {"annual_pledge": "1", "frequency": "annual", "shares": {}}
    real = submission.validate_answers

    def forge(*args, **kwargs):
        """Keep admission and all other owners real; corrupt only the normalized row."""
        result = deepcopy(real(*args, **kwargs))
        if fault == "frequency":
            result["financial"]["frequency"] = ""
        elif fault == "option":
            result["financial"]["shares"] = {OTHER: "private note"}
        elif fault == "missing":
            del result["financial"]
        else:
            result["financial"]["payment"] = "not allowed"
        return result

    monkeypatch.setattr(submission, "validate_answers", forge)
    with (
        web_login(),
        pytest.raises(IntegrityError, match="Financial"),
        transaction.atomic(),
    ):
        submission.submit_family(
            harness.request,
            harness.service,
            baseline_id=UUID(form["baseline"]),
            payload=answers,
        )
    assert not Submission.objects.exists()
    assert not SubmissionReceiptOccurrence.objects.exists()
    assert not SourceSnapshotPin.objects.filter(parent_kind="submission").exists()


def test_financial_derived_failure_rolls_back_then_can_retry(
    response_service, monkeypatch
):
    """Amount, JSON answers, pins and receipt occurrence share one transaction."""
    harness = response_service
    financial_source(harness)
    form = load_form(harness)
    answers = answers_for(form)
    answers["financial"] = {"annual_pledge": "1", "frequency": "annual", "shares": {}}

    def fail(*args):
        """Fail after inserting the response, before completing derived effects."""
        assert Submission.objects.get().annual_pledge == Decimal("1.00")
        raise RuntimeError("Synthetic derived failure")

    with monkeypatch.context() as patch, web_login():
        patch.setattr(submission, "derive_proposals", fail)
        with pytest.raises(RuntimeError, match="Synthetic derived failure"):
            submission.submit_family(
                harness.request,
                harness.service,
                baseline_id=UUID(form["baseline"]),
                payload=answers,
            )
    assert not Submission.objects.exists()
    assert not SubmissionReceiptOccurrence.objects.exists()
    assert not SourceSnapshotPin.objects.filter(parent_kind="submission").exists()
    assert respond(harness, form, answers).annual_pledge == Decimal("1.00")


def test_concurrent_financial_submission_refreshes_prior_intent(response_service):
    """A second valid Family session cannot overwrite another submission blindly."""
    harness = response_service
    financial_source(harness)
    harness = activate_response_service(harness)
    old_client = harness.client
    old_form = load_form(harness)
    old_answers = answers_for(old_form)
    old_answers["financial"] = {
        "annual_pledge": "100",
        "frequency": "weekly",
        "shares": {},
    }
    harness.client, response = login(harness.code)
    assert response.status_code == 302
    first_form = load_form(harness)
    first_answers = answers_for(first_form)
    first_answers["financial"] = {
        "annual_pledge": "200",
        "frequency": "annual",
        "shares": {},
    }
    with web_login():
        first = respond(harness, first_form, first_answers)
        response = post(
            old_client,
            "/family/submit",
            {"baseline": old_form["baseline"], "answers": old_answers},
        )
        assert response.status_code == 409, response.content
        fresh = response.json()["form"]
        assert fresh["financial"]["answers"] == first.answers["financial"]
        assert Submission.objects.count() == 1
        harness.client = old_client
        assert respond(harness, fresh, old_answers).annual_pledge == Decimal("100.00")
        first.refresh_from_db()
        assert first.annual_pledge == Decimal("200.00")


def test_all_terminal_members_keep_family_financial_intent(response_service):
    """Terminal requests remove only Member/Ministry fields, not the Family pledge."""
    harness = response_service
    financial_source(
        harness, modules=["census", "financial"], options=map(asdict, OPTIONS)
    )
    with web_login():
        form = load_form(harness)
        answers = answers_for(form)
        answers["members"] = {
            key: {"moved_household": True, "confirmed": True}
            for key in answers["members"]
        }
        answers["financial"] = {
            "annual_pledge": "10",
            "frequency": "annual",
            "shares": {OTHER: "Gift"},
        }
        first = respond(harness, form, answers)
        form = revisit(harness)
        assert all(member["request"] for member in form["members"])
        assert form["financial"]["answers"] == first.answers["financial"]
        assert respond(harness, form, answers_for(form)).annual_pledge == Decimal(
            "10.00"
        )
