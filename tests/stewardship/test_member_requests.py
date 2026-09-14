"""Pure terminal and local-identity validation with no answer persistence."""

from copy import deepcopy
from datetime import date
from uuid import uuid4

import pytest

from parishkit.stewardship.responses.answers import InvalidAnswers, validate_answers
from parishkit.stewardship.responses.comparison import (
    ComparisonValueError,
    ValueKind,
    canonical_value,
)
from parishkit.stewardship.responses.inputs import CensusInputs, FieldInput
from parishkit.stewardship.responses.member_requests import (
    MAX_PROPOSED_MEMBERS,
    InvalidMemberAnswers,
    existing_answers,
    local_member_id,
    ordinary_answers,
)
from parishkit.stewardship.responses.merge import KnownValue

from .census_factory import household, member
from .test_response_answers import family_fields

TODAY = date(2026, 9, 13)
SOURCES = {"birth_date": KnownValue(True, "1960-01-01")}


@pytest.mark.parametrize("kind", ["moved_household", "deceased_status"])
def test_terminal_confirmation_ignores_ordinary_edits_without_mutating_input(kind):
    request = {kind: True, "confirmed": True}
    if kind == "deceased_status":
        request["death_date"] = ""
    raw = (
        member(first_name=None, birth_date="secret", email={"secret": "bad"}) | request
    )
    before = deepcopy(raw)
    normalized = existing_answers(raw, SOURCES, today=TODAY)
    assert normalized == request | (
        {"death_date": None} if kind == "deceased_status" else {}
    )
    assert raw == before


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmed": False},
        {"confirmed": 1},
        {"confirmed": "true"},
        {"moved_household": True},
        {"deceased_status": False},
        {"deceased_status": 1},
        {"private": "secret"},
    ],
)
def test_terminal_choice_is_explicit_closed_and_mutually_exclusive(changes):
    raw = {"deceased_status": True, "death_date": "", "confirmed": True} | changes
    with pytest.raises(InvalidMemberAnswers) as error:
        existing_answers(raw, SOURCES, today=TODAY)
    assert "secret" not in repr(error.value.fields) + str(error.value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        1,
        True,
        "unknown",
        "1959-12-31",
        "2026-09-14",
        "2025-02-29",
        "2026-1-01",
        "0000-01-01",
    ],
)
def test_death_date_is_optional_civil_not_future_and_not_before_recorded_birth(value):
    with pytest.raises(InvalidMemberAnswers) as error:
        existing_answers(
            {"deceased_status": True, "death_date": value, "confirmed": True},
            SOURCES,
            today=TODAY,
        )
    assert set(error.value.fields) == {"death_date"}


@pytest.mark.parametrize("value", ["", "1960-01-01", "2024-02-29", "2026-09-13"])
def test_valid_death_date_and_no_in_step_birth_override(value):
    raw = {
        "deceased_status": True,
        "death_date": value,
        "confirmed": True,
        "birth_date": "2099-01-01",
    }
    assert existing_answers(raw, SOURCES, today=TODAY)["death_date"] == (value or None)


@pytest.mark.parametrize(
    "value",
    [
        None,
        1,
        "1",
        "private",
        "00000000-0000-0000-0000-000000000000",
        "{1cf34b06-48ec-4277-ad56-a3282869c417}",
        "1CF34B06-48EC-4277-AD56-A3282869C417",
    ],
)
def test_invalid_or_noncanonical_local_identity(value):
    assert not local_member_id(value)


def test_proposed_member_requires_all_ordinary_fields_and_explicit_unknown():
    assert local_member_id(str(uuid4()))
    assert (
        ordinary_answers(member(birth_date="unknown"), {}, today=TODAY)["birth_date"]
        is None
    )
    for raw in (
        {},
        member(first_name=""),
        member(birth_date=""),
        member() | {"deceased_status": True},
    ):
        with pytest.raises(InvalidMemberAnswers):
            ordinary_answers(raw, {}, today=TODAY)


def validate(members, proposed):
    """Use a closed trusted active household with one recorded birth date."""
    return validate_answers(
        {
            "family": household(),
            "members": members,
            "proposed_members": proposed,
            "ministries": {},
            "additional_information": "",
            "testing_acknowledged": False,
        },
        CensusInputs(
            10,
            (1,),
            family_fields()
            + (
                FieldInput(
                    "member", 1, "birth_date", ValueKind.DATE, SOURCES["birth_date"]
                ),
            ),
            "d" * 64,
        ),
        additional_enabled=False,
        testing=False,
        today=TODAY,
    )


def test_complete_all_terminal_household_and_proposed_member_is_valid():
    identity = str(uuid4())
    result = validate(
        {"1": {"moved_household": True, "confirmed": True}}, {identity: member()}
    )
    assert result["members"] == {"1": {"moved_household": True, "confirmed": True}}
    assert set(result["proposed_members"]) == {identity}


@pytest.mark.parametrize(
    "proposed",
    [
        None,
        [],
        {"private": {}},
        {str(uuid4()): member() for _ in range(MAX_PROPOSED_MEMBERS + 1)},
    ],
)
def test_invalid_proposed_collection_has_no_private_error_keys(
    proposed,
):
    with pytest.raises(InvalidAnswers) as error:
        validate({"1": member()}, proposed)
    assert set(error.value.fields) == {"proposed_members"}


def test_proposed_field_error_uses_validated_local_identity_and_closed_field():
    identity = str(uuid4())
    with pytest.raises(InvalidAnswers) as error:
        validate({"1": member()}, {identity: member(first_name="")})
    assert set(error.value.fields) == {f"proposed_members.{identity}.first_name"}


def test_terminal_request_cannot_omit_or_add_existing_member_identity():
    for members in (
        {},
        {"1": member(), "2": {"moved_household": True, "confirmed": True}},
    ):
        with pytest.raises(InvalidAnswers) as error:
            validate(members, {})
        assert set(error.value.fields) == {"members"}


def test_new_member_comparison_uses_typed_field_identity_not_phone_display():
    first = ordinary_answers(member(home_phone="2025550123"), {}, today=TODAY)
    second = ordinary_answers(member(home_phone="+1 (202) 555-0123"), {}, today=TODAY)
    assert canonical_value(ValueKind.MEMBER, first) == canonical_value(
        ValueKind.MEMBER, second
    )
    second["first_name"] = "Different"
    assert canonical_value(ValueKind.MEMBER, first) != canonical_value(
        ValueKind.MEMBER, second
    )
    with pytest.raises(ComparisonValueError):
        canonical_value(ValueKind.MEMBER, {"private": "invalid"})
