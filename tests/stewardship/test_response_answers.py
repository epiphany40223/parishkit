"""The final validator is total over the closed census aggregate, never a patch."""

from copy import deepcopy
from datetime import date

import pytest

from parishkit.stewardship.responses.answers import (
    ADDITIONAL_MAX_LENGTH,
    InvalidAnswers,
    validate_answers,
)
from parishkit.stewardship.responses.census import FAMILY_FIELDS
from parishkit.stewardship.responses.inputs import FORM_SCHEMA, CensusInputs, FieldInput
from parishkit.stewardship.responses.merge import KnownValue

from .census_factory import household, member


def family_fields():
    """Represent the same explicit unavailable source fields as the input owner."""
    return tuple(
        FieldInput("family", 10, field.name, field.kind, KnownValue(False))
        for field in FAMILY_FIELDS
    )


@pytest.fixture
def answers():
    return {
        "family": household(),
        "members": {
            "1": member(
                first_name=" First ",
                last_name="Last",
                email="ONE@example.org; two@example.org",
            )
        },
        "additional_information": " Text ",
        "testing_acknowledged": False,
    }


def validate(payload, **kwargs):
    """Use a trusted issued household list, not one taken from the submitted keys."""
    inputs = CensusInputs(10, (1,), family_fields(), "d" * 64)
    return validate_answers(
        payload,
        inputs,
        today=date(2026, 9, 13),
        **({"additional_enabled": True, "testing": False} | kwargs),
    )


def test_complete_answer_is_normalized_without_mutating_input(answers):
    before = deepcopy(answers)
    result = validate(answers)
    assert result == {
        "schema": FORM_SCHEMA,
        "family": {
            "home_address": None,
            "mailing_address": None,
            "mailing_same_as_home": False,
            "email_opt_out": None,
        },
        "members": {
            "1": {
                key: value or None
                for key, value in member(
                    first_name="First",
                    last_name="Last",
                    email="one@example.org, two@example.org",
                ).items()
            }
        },
        "additional_information": "Text",
    }
    assert answers == before


def test_multiple_addresses_round_trip_through_normalized_browser_representation(
    answers,
):
    """The validator must accept its own comma-joined prefill on a later visit."""
    first = validate(answers)
    answers["members"] = {
        key: {name: value or "" for name, value in fields.items()}
        for key, fields in first["members"].items()
    }
    answers["additional_information"] = first["additional_information"]
    assert validate(answers) == first


@pytest.mark.parametrize(
    "operation",
    [
        "root_extra",
        "root_missing",
        "member_missing",
        "member_extra",
        "foreign_member",
        "wrong_member_type",
    ],
)
def test_complete_closed_schema_required(answers, operation):
    if operation == "root_extra":
        answers["private-injected"] = "private"
    elif operation == "root_missing":
        del answers["additional_information"]
    elif operation == "member_missing":
        del answers["members"]["1"]["email"]
    elif operation == "member_extra":
        answers["members"]["1"]["private-injected"] = "private"
    elif operation == "foreign_member":
        answers["members"]["private-injected"] = answers["members"]["1"]
    else:
        answers["members"]["1"] = ["private"]
    with pytest.raises(InvalidAnswers) as caught:
        validate(answers)
    assert "private" not in str(caught.value) + repr(caught.value.fields)


@pytest.mark.parametrize(
    "field,value",
    [
        ("first_name", ""),
        ("last_name", "  "),
        ("first_name", "x" * 101),
        ("middle_name", None),
        ("email", "not-an-address"),
        ("email", "valid@example.org;invalid"),
        ("email", "a\0@example.org"),
        ("first_name", "\ud800"),
        ("first_name", 1),
    ],
)
def test_invalid_values_return_only_static_field_errors(answers, field, value):
    answers["members"]["1"][field] = value
    with pytest.raises(InvalidAnswers) as caught:
        validate(answers)
    assert set(caught.value.fields) == {f"members.1.{field}"}


@pytest.mark.parametrize("value", [None, 1, "yes", False])
def test_testing_requires_separate_exact_final_ack(answers, value):
    answers["testing_acknowledged"] = value
    with pytest.raises(InvalidAnswers) as caught:
        validate(answers, testing=True)
    assert "testing_acknowledged" in caught.value.fields


def test_testing_ack_is_not_stored_as_an_answer(answers):
    answers["testing_acknowledged"] = True
    result = validate(answers, testing=True)
    assert "testing_acknowledged" not in result


def test_live_form_rejects_test_ack_instead_of_changing_namespace(answers):
    answers["testing_acknowledged"] = True
    with pytest.raises(InvalidAnswers):
        validate(answers)


@pytest.mark.parametrize(
    "value", ["x" * (ADDITIONAL_MAX_LENGTH + 1), None, "text\0", {"private": "value"}]
)
def test_additional_text_is_bounded_typed_and_safe(answers, value):
    answers["additional_information"] = value
    with pytest.raises(InvalidAnswers) as caught:
        validate(answers)
    assert set(caught.value.fields) == {"additional_information"}


def test_disabled_additional_section_accepts_only_blank(answers):
    with pytest.raises(InvalidAnswers):
        validate(answers, additional_enabled=False)
    answers["additional_information"] = ""
    assert validate(answers, additional_enabled=False)["additional_information"] == ""


def test_empty_active_household_is_not_an_invented_member(answers):
    answers["members"] = {}
    result = validate_answers(
        answers,
        CensusInputs(10, (), family_fields(), "d" * 64),
        additional_enabled=True,
        testing=False,
        today=date(2026, 9, 13),
    )
    assert result["members"] == {}
