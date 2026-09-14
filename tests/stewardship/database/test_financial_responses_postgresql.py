"""Financial intent through real source, authenticated HTTP and restricted SQL."""

from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError, transaction

from parishkit.stewardship.responses import submission
from parishkit.stewardship.responses.models import (
    ProposedChange,
    Submission,
    SubmissionReceiptOccurrence,
)
from parishkit.stewardship.source.models import SourceSnapshotPin

from ..census_factory import member
from ..test_financial_answers import CHECK, OPTIONS, OTHER
from .campaign_builders import change
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
        harness,
        modules=["census", "ministry", "financial"],
        options=map(asdict, OPTIONS),
        selected=(4,),
    )
    with web_login():
        form = load_form(harness)
        assert form["ministries"]["members"]["3"]["current"] == [4]
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
        answers["ministries"] = {"members": {}, "proposed_members": {}}
        first = respond(harness, form, answers)
        form = revisit(harness)
        assert all(member["request"] for member in form["members"])
        assert form["financial"]["answers"] == first.answers["financial"]
        assert respond(harness, form, answers_for(form)).annual_pledge == Decimal(
            "10.00"
        )


@pytest.mark.parametrize("fault", ["scalar", "answer", "both"])
def test_sql_disabled_financial_boundary_rejects_injected_intent(
    response_service, monkeypatch, fault
):
    """Independent INSERT guards reject both JSON and scalar disabled-module paths."""
    harness = response_service
    form = load_form(harness)
    answers = answers_for(form)
    create = Submission.objects.create

    def forge(**values):
        """Keep Python validation intact but corrupt its final INSERT arguments."""
        if fault in {"scalar", "both"}:
            values["annual_pledge"] = Decimal("1.00")
        if fault in {"answer", "both"}:
            values["answers"] = deepcopy(values["answers"])
            values["answers"]["financial"] = {
                "annual_pledge": "1.00",
                "frequency": "annual",
                "shares": {},
            }
        return create(**values)

    monkeypatch.setattr(Submission.objects, "create", forge)
    with (
        web_login(),
        pytest.raises(IntegrityError, match="Submission|Disabled financial"),
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


@pytest.mark.parametrize("proposed_count", [0, 1, 2])
def test_census_disabled_financial_wording_keeps_private_effective_count(
    response_service, proposed_count
):
    """Preserved census history supplies only a count, not disabled census details."""
    harness = response_service
    financial_source(harness, modules=["census", "financial"])
    form = load_form(harness)
    answers = answers_for(form)
    answers["members"] = {
        key: {"moved_household": True, "confirmed": True} for key in answers["members"]
    }
    answers["proposed_members"] = {
        str(uuid4()): member() for _ in range(proposed_count)
    }
    answers["financial"] = {"annual_pledge": "0", "frequency": "", "shares": {}}
    respond(harness, form, answers)
    financial_source(harness, modules=["financial"])
    with web_login():
        for _ in range(2):
            form = revisit(harness)
            assert form["effective_member_count"] == proposed_count
            assert form["proposed_members"] == [] and form["new_member_fields"] == []
            assert all(
                row["request"] is None and row["fields"] == []
                for row in form["members"]
            )
            assert respond(harness, form, answers_for(form)).annual_pledge == 0


def test_parish_name_change_requires_financial_label_review(response_service):
    """A submission cannot reference a new parish label without reviewing it."""
    harness = response_service
    financial_source(
        harness,
        options=[{"id": CHECK, "label": "{{ parish_name }} gift", "free_text": False}],
    )
    form = load_form(harness)
    answers = answers_for(form)
    answers["financial"] = {
        "annual_pledge": "0",
        "frequency": "",
        "shares": {CHECK: ""},
    }
    store = harness.service.store
    version = store.active()
    result = change(
        store,
        version,
        uuid4(),
        [
            {
                "operation": "update",
                "section": "parish",
                "id": version.document()["sections"]["parish"][0]["id"],
                "values": {"name": "Renamed Sample Parish"},
            }
        ],
    )
    assert result.state == "applied"
    with web_login():
        response = post(
            harness.client,
            "/family/submit",
            {"baseline": form["baseline"], "answers": answers},
        )
        assert response.status_code == 409, response.content
        fresh = response.json()["form"]
        assert set(fresh["financial"]["options"][0]["labels"].values()) == {
            "Renamed Sample Parish gift"
        }
        assert not Submission.objects.exists()
        assert respond(harness, fresh, answers).annual_pledge == 0


def test_new_giving_timestamp_without_value_changes_does_not_force_review(
    response_service,
):
    """An unchanged full refresh accepts with distinct reviewed/validation snapshots."""
    harness = response_service
    old, _ = financial_source(harness)
    form = load_form(harness)
    answers = answers_for(form)
    answers["financial"] = {"annual_pledge": "0", "frequency": "", "shares": {}}
    new, _ = financial_source(harness)
    assert old.pk != new.pk and old.started_at != new.started_at
    with web_login():
        result = respond(harness, form, answers)
        assert result.reviewed_source_id == old.pk
        assert result.validation_source_id == new.pk
