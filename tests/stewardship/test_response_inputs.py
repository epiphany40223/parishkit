"""Enumerated dependencies of the first executable census form contract."""

from copy import deepcopy

import pytest

from parishkit.stewardship.responses.inputs import (
    MEMBER_FIELDS,
    FormInputsUnavailable,
    census_inputs,
)


@pytest.fixture
def inputs():
    """A scoped synthetic Family with one active and one inactive household Member."""
    return {
        "family": {
            "familyDUID": 1,
            "portal_eligible": True,
            "firstName": "A",
            "lastName": "Family",
            "mailingName": "A Family",
            "envelopeNumber": 0,
        },
        "members": [
            {
                "memberDUID": 10,
                "family_key": "1",
                "active": True,
                "deceased": False,
                "firstName": "A",
                "middleName": "",
                "lastName": "Family",
            },
            {
                "memberDUID": 11,
                "family_key": "1",
                "active": False,
                "deceased": False,
                "firstName": "Inactive",
                "lastName": "Family",
            },
        ],
        "contacts": {
            "10": {
                "owner_kind": "member",
                "owner_key": "10",
                "available": ["email", "home"],
                "emails": [{"value": "a@example.org", "valid": True}],
                "phones": {"home": "5025550100"},
            }
        },
        "configuration": {
            "modules": ["census"],
            "name": "Census",
            "start_date": "2026-10-01",
            "end_date": "2026-10-31",
            "timezone": "America/New_York",
            "additional_information": True,
            "content_versions": {"welcome": "v1", "review": "r1"},
        },
    }


def digest(inputs):
    """Use the same complete builder as issuance and final-validation adapters."""
    return census_inputs(**inputs).projection_digest


@pytest.mark.parametrize("field", MEMBER_FIELDS, ids=lambda field: field.name)
def test_every_editable_ui_field_is_a_dependency(inputs, field):
    before = digest(inputs)
    if field.name == "email":
        inputs["contacts"]["10"]["emails"][0]["value"] = "changed@example.org"
    else:
        inputs["members"][0][field.source_name] = "changed"
    assert digest(inputs) != before
    projected = census_inputs(**inputs)
    assert {value.field for value in projected.fields if value.entity == "member"} == {
        field.name for field in MEMBER_FIELDS
    }


@pytest.mark.parametrize(
    "field", ["firstName", "lastName", "mailingName", "envelopeNumber"]
)
def test_every_displayed_family_value_is_a_dependency(inputs, field):
    before = digest(inputs)
    inputs["family"][field] = 2 if field == "envelopeNumber" else "changed"
    assert digest(inputs) != before


@pytest.mark.parametrize(
    "operation", ["add", "remove", "deactivate", "activate", "deceased"]
)
def test_membership_changes_are_not_limited_to_original_ids(inputs, operation):
    before = digest(inputs)
    if operation == "add":
        member = {**inputs["members"][0], "memberDUID": 12}
        inputs["members"].append(member)
    elif operation == "remove":
        inputs["members"].pop(0)
    elif operation == "deactivate":
        inputs["members"][0]["active"] = False
    elif operation == "activate":
        inputs["members"][1]["active"] = True
    else:
        inputs["members"][0]["deceased"] = True
    assert digest(inputs) != before


def test_canonical_equivalence_and_order_do_not_invalidate(inputs):
    before = digest(inputs)
    inputs["members"].reverse()
    inputs["members"][1]["firstName"] = " A "
    inputs["contacts"]["10"]["emails"][0]["value"] = "A@EXAMPLE.ORG"
    assert digest(inputs) == before


def test_unrelated_values_are_not_dependencies(inputs):
    before = digest(inputs)
    inputs["members"][1]["firstName"] = "Other inactive name"
    inputs["members"][0]["birthdate"] = "2000-01-01"  # Not in this version's UI.
    inputs["contacts"]["10"]["phones"]["home"] = "5025550101"
    inputs["contacts"]["999"] = {"unrelated": "private"}
    inputs["family"]["modified_at"] = "later"
    inputs["configuration"]["email_schedule"] = "later"
    inputs["configuration"]["content_versions"]["admin"] = "new"
    assert digest(inputs) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "New census"),
        ("timezone", "America/Chicago"),
        ("additional_information", False),
    ],
)
def test_relevant_definition_changes_require_review(inputs, field, value):
    before = digest(inputs)
    inputs["configuration"][field] = value
    assert digest(inputs) != before


def test_content_versions_require_review(inputs):
    before = digest(inputs)
    inputs["configuration"]["content_versions"]["review"] = "r2"
    assert digest(inputs) != before


@pytest.mark.parametrize(
    "change", ["missing_field", "missing_contact", "unavailable_contact"]
)
def test_unavailability_is_not_a_fabricated_blank(inputs, change):
    inputs["members"][0]["middleName"] = None
    inputs["contacts"]["10"]["emails"] = []
    before = digest(inputs)
    if change == "missing_field":
        del inputs["members"][0]["middleName"]
    elif change == "missing_contact":
        del inputs["contacts"]["10"]
    else:
        inputs["contacts"]["10"]["available"].remove("email")
    assert digest(inputs) != before


@pytest.mark.parametrize(
    "violation",
    [
        "foreign_member",
        "foreign_contact",
        "duplicate",
        "ineligible",
        "unsupported_module",
    ],
)
def test_untrusted_or_unsupported_scope_is_rejected(inputs, violation):
    if violation == "foreign_member":
        inputs["members"][0]["family_key"] = "2"
    elif violation == "foreign_contact":
        inputs["contacts"]["10"]["owner_key"] = "11"
    elif violation == "duplicate":
        inputs["members"].append(deepcopy(inputs["members"][0]))
    elif violation == "ineligible":
        inputs["family"]["portal_eligible"] = False
    else:
        inputs["configuration"]["modules"].append("financial")
    with pytest.raises(
        FormInputsUnavailable, match="^The Family form inputs are unavailable.$"
    ):
        census_inputs(**inputs)


def test_builder_never_alters_provider_inputs(inputs):
    original = deepcopy(inputs)
    census_inputs(**inputs)
    assert inputs == original
