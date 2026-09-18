"""Existing Member fields exercised through exact-role HTTP and independent SQL."""

import json
from copy import deepcopy

import pytest
from django.db import IntegrityError, connection

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.responses.comparison import (
    ValueKind,
    canonical_value,
    phone_record,
)
from parishkit.stewardship.responses.member_census import MEMBER_FIELDS
from parishkit.stewardship.responses.models import ProposedChange, Submission
from parishkit.stewardship.source.models import SourceSnapshotPin

from .response_builders import response_source
from .test_background_grants_postgresql import task_login
from .test_family_auth_postgresql import login
from .test_response_http_postgresql import answers_for, load_form, post
from .test_response_submission_postgresql import form_and_answers, submit
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_families_postgresql import prepare, promote

pytestmark = pytest.mark.django_db(transaction=True)

CHANGES = {
    "prefix": "Dr.",
    "first_name": "Updated",
    "middle_name": "Middle update",
    "last_name": "Updated surname",
    "suffix": "Jr.",
    "nickname": "Nick",
    "maiden_name": "Prior surname",
    "birth_date": "1980-02-29",
    "gender": "Female",
    "email": "changed@example.org",
    "home_phone": "+44 20 8366 1177",
    "mobile_phone": "202-555-0199 x12",
    "work_phone": "+33 1 42 68 53 00",
    "marital_status": "Widowed",
    "language": "French",
}


def field(form, name):
    """Use stable field identity, not presentation index."""
    return next(item for item in form["members"][0]["fields"] if item["name"] == name)


def test_all_existing_fields_http_and_revisit_under_exact_web_role(
    live_response_service,
):
    """Keep complete census changes Family-attributed without source writes."""
    harness = live_response_service
    with web_login():
        form = load_form(harness)
        assert form["members"][0]["relationship"] == "Head"
        assert {item["name"] for item in form["members"][0]["fields"]} == set(CHANGES)
        answers = answers_for(form)
        answers["members"]["3"].update(CHANGES)
        response = post(
            harness.client,
            "/family/submit",
            {"baseline": form["baseline"], "answers": answers},
        )
        assert response.status_code == 200, response.content
        harness.client, result = login(harness.code, harness.client)
        assert result.status_code == 302
        refreshed = load_form(harness)
        assert answers_for(refreshed) == answers
        assert all(item["changed"] for item in refreshed["members"][0]["fields"])
        response = post(
            harness.client,
            "/family/submit",
            {"baseline": refreshed["baseline"], "answers": answers_for(refreshed)},
        )
        assert response.status_code == 200, response.content
    first, second = Submission.objects.order_by("family_version")
    assert first.answers == second.answers
    assert ProposedChange.objects.filter(
        submission=first, execution="superseded"
    ).count() == len(CHANGES)
    current = list(ProposedChange.objects.filter(submission=second))
    assert {row.field for row in current} == set(CHANGES)
    assert all(
        row.entity_kind == "member"
        and row.entity_key == "3"
        and row.decision == "unreviewed"
        for row in current
    )
    assert {row.field for row in current if row.handling == "manual"} == {
        "prefix",
        "suffix",
        "marital_status",
    }
    assert second.answers["members"]["3"]["home_phone"] == phone_record(
        CHANGES["home_phone"]
    )


@pytest.mark.parametrize("available", [False, True])
def test_explicit_unknown_birth_date_survives_repeat_visit(
    live_response_service, available
):
    """Retain explicit Unknown even without an unavailable-source proposal."""
    harness = live_response_service
    data = response_source()
    if available:
        data.members[3]["birthdate"] = None
    else:
        del data.members[3]["birthdate"]
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    form = load_form(harness)
    assert field(form, "birth_date")["value"] == ""
    answers = answers_for(form)
    answers["members"]["3"]["birth_date"] = "unknown"
    response = post(
        harness.client,
        "/family/submit",
        {"baseline": form["baseline"], "answers": answers},
    )
    assert response.status_code == 200, response.content
    assert Submission.objects.get().answers["members"]["3"]["birth_date"] is None
    assert not ProposedChange.objects.exists()
    harness.client, result = login(harness.code, harness.client)
    assert result.status_code == 302
    assert field(load_form(harness), "birth_date")["value"] == "unknown"


def test_expanded_sql_source_reconstruction_matches_python(response_service):
    """Source values and availability must match independently in both languages."""
    form, _ = form_and_answers(response_service)
    with connection.cursor() as cursor:
        # Every field reads the same immutable snapshot; creating a campaign
        # and authenticating again for each field adds no isolation coverage.
        for definition in MEMBER_FIELDS:
            source = next(
                item.source
                for item in form.inputs.fields
                if item.entity == "member" and item.field == definition.name
            )
            cursor.execute(
                "SELECT stewardship_response_field_source_v1(%s,%s,%s,%s)",
                [
                    form.baseline.source_id,
                    form.baseline.family_id,
                    "3",
                    definition.name,
                ],
            )
            result = cursor.fetchone()[0]
            if isinstance(result, str):
                result = json.loads(result)
            assert result == {
                "available": source.available,
                "value": source.value,
            }, definition.name


@pytest.mark.parametrize(
    "display",
    [
        "2025550123",
        "+1 (202) 555-0123",
        "+44 20 8366 1177",
        "+33 1 42 68 53 00 ext. 123",
        "2025550123 x7",
        "ask at office",
        "555 123",
        "",
        " \u00a02025550123\u00a0 ",
    ],
)
def test_sql_phone_display_and_comparison_match_python(display):
    """Formatting, national context, extension and malformed legacy identity agree."""
    record = phone_record(display)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_response_phone_record_v1(%s), "
            "stewardship_response_comparison_v1('home_phone',%s::jsonb)",
            [display, json.dumps(record)],
        )
        stored, comparison = cursor.fetchone()
    assert (json.loads(stored) if isinstance(stored, str) else stored) == record
    assert json.loads(comparison) == list(canonical_value(ValueKind.PHONE, record))


@pytest.mark.parametrize(
    "name,value",
    [
        ("first_name", None),
        ("last_name", ""),
        ("birth_date", "3000-01-01"),
        ("birth_date", "2025-02-29"),
        ("gender", "Invented"),
        ("language", ""),
        ("prefix", " Not normalized "),
        ("nickname", "x" * 101),
        ("marital_status", "Invented"),
        ("home_phone", {"normalized": "+12025550199", "display": "2025550123"}),
        ("mobile_phone", {"normalized": None, "display": "invented invalid phone"}),
    ],
)
def test_sql_rejects_forged_normalized_member_answer(
    response_service, monkeypatch, name, value
):
    """A defective answer adapter cannot evade the independent typed guard."""
    from parishkit.stewardship.responses import submission as owner

    original = owner.validate_answers
    form, answers = form_and_answers(response_service)

    def forged(*args, **kwargs):
        """Alter only the already-normalized answer immediately before insertion."""
        result = deepcopy(original(*args, **kwargs))
        result["members"]["3"][name] = value
        return result

    monkeypatch.setattr(owner, "validate_answers", forged)
    with web_login(), pytest.raises(IntegrityError):
        submit(response_service, form, answers)
    assert not Submission.objects.exists()
    assert not ProposedChange.objects.exists()


@pytest.mark.parametrize(
    "change", ["missing_field", "extra_field", "missing_member", "extra_member"]
)
def test_sql_rejects_incomplete_or_foreign_member_aggregate(
    response_service, monkeypatch, change
):
    """The complete household is derived from retained source, never submitted keys."""
    from parishkit.stewardship.responses import submission as owner

    original = owner.validate_answers
    form, answers = form_and_answers(response_service)

    def forged(*args, **kwargs):
        """Simulate an incomplete aggregate bypassing only the Python adapter."""
        result = deepcopy(original(*args, **kwargs))
        if change == "missing_field":
            del result["members"]["3"]["prefix"]
        elif change == "extra_field":
            result["members"]["3"]["private"] = "injected"
        elif change == "missing_member":
            result["members"].clear()
        else:
            result["members"]["4"] = deepcopy(result["members"]["3"])
        return result

    monkeypatch.setattr(owner, "validate_answers", forged)
    with web_login(), pytest.raises(IntegrityError):
        submit(response_service, form, answers)
    assert not Submission.objects.exists()


@pytest.mark.parametrize(
    "name,answer,source_name,source_value",
    [
        ("birth_date", "unknown", "birthdate", None),
        ("home_phone", "+44 20 8366 1177", "homePhone", "+44 (20) 8366-1177"),
        ("home_phone", "+43 1 234", "homePhone", "+43 (1) 234"),
        ("gender", "Female", "sex", " FEMALE "),
        ("prefix", "Dr.", "salutation", "Dr."),
    ],
)
def test_typed_member_proposals_resolve_only_when_upstream_catches_up(
    live_response_service, name, answer, source_name, source_value
):
    """Reconcile civil nulls, phones, enums and manual text as typed values."""
    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    answers["members"]["3"][name] = answer
    row = submit(harness, form, answers).submission
    proposal = ProposedChange.objects.get(submission=row)
    assert proposal.execution == "pending"
    data = response_source()
    data.members[3][source_name] = source_value
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    proposal.refresh_from_db()
    assert proposal.execution == "resolved_upstream"
    harness.client, result = login(harness.code, harness.client)
    assert result.status_code == 302
    assert not field(load_form(harness), name)["changed"]


def test_unrelated_unchanged_unknown_is_not_reapplied_after_source_arrives(
    live_response_service,
):
    """An old complete Unknown answer is not an intent to erase later parish data."""
    harness = live_response_service
    data = response_source()
    del data.members[3]["birthdate"]
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    form = load_form(harness)
    answers = answers_for(form)
    answers["members"]["3"]["birth_date"] = "unknown"
    result = post(
        harness.client,
        "/family/submit",
        {"baseline": form["baseline"], "answers": answers},
    )
    assert result.status_code == 200
    assert not ProposedChange.objects.exists()
    snapshot, claim = prepare(response_source())
    promote(snapshot, claim, harness.campaign, harness.rings)
    harness.client, result = login(harness.code, harness.client)
    assert result.status_code == 302
    actual = field(load_form(harness), "birth_date")
    assert actual["value"] == "1960-01-01" and not actual["changed"]


@pytest.mark.parametrize(
    "value", ["before\u0085after", "before\u2028after", "before\u2029after"]
)
def test_member_separators_return_private_field_errors(response_service, value):
    form = load_form(response_service)
    answers = answers_for(form)
    answers["members"]["3"]["nickname"] = value
    response = post(
        response_service.client,
        "/family/submit",
        {"baseline": form["baseline"], "answers": answers},
    )
    assert response.status_code == 422
    assert set(response.json()["fields"]) == {"members.3.nickname"}
    assert b"before" not in response.content
    assert not Submission.objects.exists()


@pytest.mark.parametrize(
    "name,value",
    [
        ("homePhone", "x" * 101),
        ("homePhone", "x" * 257),
        ("sex", "x" * 101),
        ("firstName", "x" * 101),
    ],
)
def test_oversized_source_is_a_controlled_form_unavailable(
    response_service, name, value
):
    harness = response_service
    data = response_source()
    data.members[3][name] = value
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    with web_login():
        response = post(harness.client, "/family/form", {"testing_acknowledged": True})
    assert response.status_code == 503
    assert response.json() == {"error": "temporarily_unavailable"}
    assert value.encode() not in response.content
    assert not Submission.objects.exists()
    event = OperationalLog.objects.get(event="source_member_unusable")
    assert event.level == "WARNING" and event.schema == "member_source"
    assert event.context == {
        "family_duid": 1,
        "member_duid": 3,
        "field": {
            "homePhone": "home_phone",
            "sex": "gender",
            "firstName": "first_name",
        }[name],
    }


def test_legacy_enum_outer_whitespace_is_an_unchanged_value(response_service):
    """A bounded unsupported source choice stays usable through normalization."""
    harness = response_service
    data = response_source()
    data.members[3].update(sex=" Legacy choice ", maritalStatus=" Prior choice ")
    snapshot, claim = prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)
    with web_login():
        form = load_form(harness)
        response = post(
            harness.client,
            "/family/submit",
            {"baseline": form["baseline"], "answers": answers_for(form)},
        )
        assert response.status_code == 200, response.content
    assert not ProposedChange.objects.exists()
    assert Submission.objects.get().answers["members"]["3"]["gender"] == "Legacy choice"


@pytest.mark.parametrize("recovery", ["corrected", "absent"])
def test_unusable_pending_source_field_does_not_block_other_refresh(
    live_response_service,
    recovery,
):
    """Block an unusable comparison, retain provenance, then resolve on correction."""
    harness = live_response_service
    form, answers = form_and_answers(harness)
    answers["testing_acknowledged"] = False
    answers["members"]["3"]["home_phone"] = "+44 20 8366 1177"
    row = submit(harness, form, answers).submission
    proposal = ProposedChange.objects.get(submission=row)
    previous = proposal.current_source_id
    data = response_source()
    data.members[3]["homePhone"] = "x" * 257
    data.members[3]["firstName"] = "Another valid update"
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promoted = promote(snapshot, claim, harness.campaign, harness.rings)
    proposal.refresh_from_db()
    assert promoted.pk != previous and proposal.current_source_id == promoted.pk
    assert proposal.execution == "conflict"
    assert not proposal.current_available and proposal.current_value is None
    assert OperationalLog.objects.get(event="source_member_unusable").context == {
        "family_duid": 1,
        "member_duid": 3,
        "field": "home_phone",
    }
    assert set(
        SourceSnapshotPin.objects.filter(
            parent_kind="submission", parent_id=row.pk
        ).values_list("snapshot_id", flat=True)
    ) == {previous, promoted.pk}
    version = proposal.version
    data.members[3]["firstName"] = "Another unrelated update"
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    proposal.refresh_from_db()
    assert proposal.version == version and proposal.current_source_id == promoted.pk
    assert OperationalLog.objects.filter(event="source_member_unusable").count() == 1
    harness.client, result = login(harness.code, harness.client)
    assert result.status_code == 302
    assert (
        post(
            harness.client, "/family/form", {"testing_acknowledged": False}
        ).status_code
        == 503
    )
    if recovery == "corrected":
        data.members[3]["homePhone"] = "+44 20 8366 1177"
    else:
        del data.members[3]["homePhone"]
    snapshot, claim = prepare(data)
    with task_login(ServiceRole.WORKER, exact=True):
        promote(snapshot, claim, harness.campaign, harness.rings)
    proposal.refresh_from_db()
    assert proposal.execution == (
        "resolved_upstream" if recovery == "corrected" else "pending"
    )
    assert proposal.current_available is (recovery == "corrected")
    assert proposal.current_source_id == snapshot.pk
    assert set(
        SourceSnapshotPin.objects.filter(
            parent_kind="submission", parent_id=row.pk
        ).values_list("snapshot_id", flat=True)
    ) == {previous, snapshot.pk}
