"""Enumerated dependencies of the first executable census form contract."""

from copy import deepcopy

import pytest

from parishkit.stewardship.responses.inputs import (
    FAMILY_FIELDS,
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


def test_household_fields_preserve_explicit_unavailable_source(inputs):
    """Primary/postal fields cannot establish home, mailing or email intent."""
    before = digest(inputs)
    inputs["family"].update(
        primaryAddress1="Private source contact",
        primaryCity="A locality",
        sendNoMail=True,
        registrationDate="2020-01-01",
        homeAddressLine1="unverified",
    )
    assert digest(inputs) == before
    fields = {
        field.field: field
        for field in census_inputs(**inputs).fields
        if field.entity == "family"
    }
    for name in [*(field.name for field in FAMILY_FIELDS), "registration_date"]:
        assert not fields[name].source.available and fields[name].source.value is None


@pytest.mark.parametrize("change", ["country", "limit", "region"])
def test_household_validation_definitions_are_relevant_dependencies(
    inputs, monkeypatch, change
):
    """An open form must re-review changes to offered countries or address rules."""
    from parishkit.stewardship.responses import inputs as owner

    before = digest(inputs)
    if change == "country":
        choices = owner.country_choices()
        monkeypatch.setattr(owner, "country_choices", lambda: choices[:-1])
    elif change == "limit":
        monkeypatch.setattr(
            owner, "ADDRESS_LIMITS", dict(owner.ADDRESS_LIMITS) | {"line1": 201}
        )
    else:
        regions = owner.us_regions()
        monkeypatch.setattr(owner, "us_regions", lambda: regions - {"KY"})
    assert digest(inputs) != before


@pytest.mark.parametrize("field", MEMBER_FIELDS, ids=lambda field: field.name)
def test_every_editable_ui_field_is_a_dependency(inputs, field):
    before = digest(inputs)
    if field.name == "email":
        inputs["contacts"]["10"]["emails"][0]["value"] = "changed@example.org"
    elif field.kind.value == "phone":
        inputs["contacts"]["10"]["available"].append(field.source_name)
        inputs["contacts"]["10"]["phones"][field.source_name] = "+12025550123"
    else:
        inputs["members"][0][field.source_name] = (
            "2000-01-01" if field.kind.value == "date" else "changed"
        )
    assert digest(inputs) != before
    projected = census_inputs(**inputs)
    assert {value.field for value in projected.fields if value.entity == "member"} == {
        field.name for field in MEMBER_FIELDS
    } | {"moved_household", "deceased_status", "death_date"}


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
    inputs["members"][0]["unrelated_provider_metadata"] = "changed"
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


def test_relationship_context_is_a_displayed_dependency(inputs):
    before = digest(inputs)
    inputs["members"][0]["memberType"] = "Head"
    assert digest(inputs) != before


def test_phone_punctuation_equivalence_does_not_invalidate(inputs):
    before = digest(inputs)
    inputs["contacts"]["10"]["phones"]["home"] = "+1 (502) 555-0100"
    assert digest(inputs) == before
    inputs["contacts"]["10"]["phones"]["home"] += " x7"
    assert digest(inputs) != before


@pytest.mark.parametrize(
    "name,value",
    [("firstName", "x" * 101), ("sex", "x" * 101), ("birthdate", "2025-02-29")],
)
def test_unusable_member_source_never_issues_a_baseline(inputs, name, value):
    inputs["members"][0][name] = value
    with pytest.raises(FormInputsUnavailable):
        census_inputs(**inputs)


@pytest.mark.parametrize("length", [101, 257, 8192])
def test_oversized_source_phone_has_controlled_unavailability(inputs, length):
    inputs["contacts"]["10"]["phones"]["home"] = "x" * length
    with pytest.raises(FormInputsUnavailable):
        census_inputs(**inputs)
