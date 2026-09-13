"""Real household HTTP, proposal ownership and source-bound SQL acceptance."""

import json
from copy import deepcopy
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection

from parishkit.stewardship.responses.comparison import ValueKind, canonical_value
from parishkit.stewardship.responses.models import ProposedChange, Submission

from ..census_factory import address
from .response_builders import response_source
from .test_family_auth_postgresql import login
from .test_response_http_postgresql import answers_for, load_form, post
from .test_response_submission_postgresql import form_and_answers, submit
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("opt_out", [False, True])
def test_household_http_complete_response_and_revisit(live_response_service, opt_out):
    """A Family request remains pending, complete and visible only as effective data."""
    harness = live_response_service
    with web_login():
        form = load_form(harness)
        assert form["family"]["registration_date"] is None
        assert all(not field["available"] for field in form["household"]["fields"])
        answers = answers_for(form)
        answers["family"].update(
            home_address=address(),
            mailing_address=address(),
            mailing_same_as_home=True,
            email_opt_out=opt_out,
        )
        response = post(
            harness.client,
            "/family/submit",
            {
                "baseline": form["baseline"],
                "answers": answers,
            },
        )
        assert response.status_code == 200, response.content
        assert harness.client.get("/family/").status_code == 302
        harness.client, result = login(harness.code, harness.client)
        assert result.status_code == 302
        revisited = load_form(harness)
        assert answers_for(revisited) == answers
        assert all(field["changed"] for field in revisited["household"]["fields"])
        response = post(
            harness.client,
            "/family/submit",
            {
                "baseline": revisited["baseline"],
                "answers": answers_for(revisited),
            },
        )
        assert response.status_code == 200, response.content
    first, second = Submission.objects.order_by("family_version")
    assert first.answers == second.answers
    assert (
        ProposedChange.objects.filter(submission=first, execution="superseded").count()
        == 3
    )
    current = list(ProposedChange.objects.filter(submission=second))
    assert len(current) == 3
    assert all(row.entity_kind == "family" and row.entity_key == "1" for row in current)
    assert all(
        not row.baseline_available and not row.current_available for row in current
    )
    assert all(
        row.execution == "pending" and row.decision == "unreviewed" for row in current
    )
    assert {row.field: row.handling for row in current} == {
        "home_address": "api",
        "mailing_address": "api",
        "email_opt_out": "manual",
    }


@pytest.mark.parametrize("inactive", [False, True])
def test_source_refresh_never_invents_household_values(live_response_service, inactive):
    """Preserve household intent, including an inactive Family's history."""
    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    answers["family"]["home_address"] = address()
    first = submit(harness, form, answers).submission
    data = response_source()
    data.families[1]["sendNoMail"] = True
    if inactive:
        data.family_groups[7] = "Inactive"
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    row = ProposedChange.objects.get(submission=first)
    first.family.refresh_from_db()
    assert first.family.portal_eligible is (not inactive)
    assert row.execution == "pending"
    assert not row.current_available and row.current_value is None
    assert row.submitted_value == address()


@pytest.mark.parametrize(
    "override",
    [
        {"entity_key": "2"},
        {"entity_key": "999999"},
        {"actor_id": None},
        {"actor_id": uuid4()},
        {"entity_kind": "member"},
        {"baseline_available": True},
        {"baseline_value": address()},
        {"current_available": True},
        {"current_value": address()},
        {"submitted_value": address(line1="Forged address")},
        {"handling": "manual"},
        {"decision": "approved"},
        {"execution": "conflict"},
    ],
)
def test_household_proposal_rejects_forged_ownership_or_values(
    response_service, monkeypatch, override
):
    """Actual web grants cannot impersonate another Family or derive a false change."""
    form, answers = form_and_answers(response_service)
    answers["family"]["home_address"] = address()
    manager = ProposedChange.objects
    original = manager.create

    def altered(**values):
        """Inject the faulty owner output immediately before PostgreSQL insertion."""
        return original(**(values | override))

    monkeypatch.setattr(manager, "create", altered)
    with web_login(), pytest.raises(IntegrityError):
        submit(response_service, form, answers)
    assert not Submission.objects.exists()
    assert not ProposedChange.objects.exists()


@pytest.mark.parametrize(
    "field,value,kind",
    [
        ("home_address", address(), ValueKind.ADDRESS),
        ("mailing_address", address(line1="  Straße e\u0301  "), ValueKind.ADDRESS),
        ("home_address", None, ValueKind.ADDRESS),
        ("email_opt_out", True, ValueKind.BOOLEAN),
        ("email_opt_out", False, ValueKind.BOOLEAN),
        ("email_opt_out", None, ValueKind.BOOLEAN),
    ],
)
def test_household_sql_comparison_matches_python(field, value, kind):
    """SQL object serialization compares the same typed content, not display text."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_response_comparison_v1(%s,%s::jsonb)",
            [field, json.dumps(value)],
        )
        result = cursor.fetchone()[0]
    actual = json.loads(result) if result is not None else None
    expected = canonical_value(kind, value)
    assert actual == (
        dict(expected)
        if kind is ValueKind.ADDRESS and expected is not None
        else expected
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("home_address", "private value"),
        ("home_address", {"line1": "Missing components"}),
        ("mailing_address", address(country=12)),
        ("email_opt_out", 1),
        ("email_opt_out", "false"),
        ("unknown", None),
    ],
)
def test_household_sql_rejects_untyped_comparison(field, value):
    """JSON scalars cannot coerce booleans or incomplete address components."""
    with pytest.raises(IntegrityError), connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_response_comparison_v1(%s,%s::jsonb)",
            [field, json.dumps(value)],
        )


def test_invalid_household_http_errors_do_not_echo_private_values(response_service):
    """Invalid addresses return static paths, keep the session and save nothing."""
    form = load_form(response_service)
    answers = answers_for(form)
    answers["family"]["home_address"] = address(postal_code="private-invalid-postal")
    response = post(
        response_service.client,
        "/family/submit",
        {
            "baseline": form["baseline"],
            "answers": answers,
        },
    )
    assert response.status_code == 422
    assert "family.home_address.postal_code" in response.json()["fields"]
    assert b"private-invalid-postal" not in response.content
    assert not Submission.objects.exists()
    assert response_service.client.get("/family/").status_code == 200


@pytest.mark.parametrize(
    "change",
    [
        {"country": "ZZ"},
        {"line1": ""},
        {"city": ""},
        {"region": "XX"},
        {"postal_code": "4022"},
        {"line1": "x" * 201},
        {"line1": "line\nbreak"},
        {"line1": " Not normalized "},
        {"country": "us"},
    ],
)
def test_sql_rejects_address_corruption_after_python_validation(
    response_service, monkeypatch, change
):
    """Bypassing the application validator cannot commit a malformed response."""
    form, answers = form_and_answers(response_service)
    answers["family"]["home_address"] = address()
    manager = Submission.objects
    original = manager.create

    def altered(**values):
        """Simulate corruption between successful Python validation and INSERT."""
        values["answers"] = deepcopy(values["answers"])
        values["answers"]["family"]["home_address"].update(change)
        return original(**values)

    monkeypatch.setattr(manager, "create", altered)
    with web_login(), pytest.raises(IntegrityError):
        submit(response_service, form, answers)
    assert not Submission.objects.exists()


def test_sql_country_and_region_vocabulary_matches_pinned_python():
    """Exercise every ISO country and US region, including rejection of all gaps."""
    from itertools import product
    from string import ascii_uppercase

    from django.db import transaction

    from parishkit.stewardship.responses.census import (
        country_choices,
        us_regions,
        validate_address,
    )

    countries = {code for code, _ in country_choices()}
    regions = us_regions()
    with connection.cursor() as cursor:
        for letters in product(ascii_uppercase, repeat=2):
            code = "".join(letters)
            for component, allowed in (("country", countries), ("region", regions)):
                value = address(**{component: code})
                if code in allowed:
                    normalized = validate_address(value)
                    cursor.execute(
                        "SELECT stewardship_response_address_guard_v1(%s::jsonb)",
                        [json.dumps(normalized)],
                    )
                else:
                    with pytest.raises(IntegrityError), transaction.atomic():
                        cursor.execute(
                            "SELECT stewardship_response_address_guard_v1(%s::jsonb)",
                            [json.dumps(value)],
                        )
