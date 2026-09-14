"""Closed Ministry choices use local policy and Family-scoped source evidence."""

from copy import deepcopy
from uuid import uuid4

import pytest

from parishkit.stewardship.responses.ministry import (
    InvalidMinistryAnswers,
    InvalidMinistrySource,
    ministry_inputs,
    validate_ministry_answers,
)


def inputs(**overrides):
    """One current Ministry, one join option and one locally hidden catalog row."""
    values = {
        "catalog": [
            {"id": 4, "name": "Choir", "catalog_present": True, "active": False},
            {"id": 9, "name": "Food pantry", "catalog_present": True},
            {"id": 10, "name": "Hidden", "catalog_present": True},
        ],
        "roster": [
            {"member_key": "3", "ministry_key": "4", "current": True},
            {"member_key": "3", "ministry_key": "10", "current": True},
        ],
        "member_duids": (3,),
        "selected_duids": [4, 9, 10, 20],
        "document": {
            "sections": {
                "ministries": [
                    {
                        "values": {
                            "organization_id": 5,
                            "ministry_duid": 10,
                            "active": False,
                        }
                    }
                ]
            }
        },
        "organization_id": 5,
    }
    return ministry_inputs(**(values | overrides))


def answer():
    """A complete untouched existing-Member Ministry answer."""
    return {"members": {"3": {"join": [], "leave": []}}, "proposed_members": {}}


def validate(value, projection=None, *, terminal=(), proposed=()):
    """Exercise the pure owner with already validated census identity sets."""
    return validate_ministry_answers(
        value,
        inputs() if projection is None else projection,
        terminal_members=frozenset(terminal),
        proposed_members=frozenset(proposed),
    )


def test_visibility_intersects_catalog_selection_and_local_activity():
    """Upstream flags and absent selected IDs cannot override local activity."""
    projection = inputs()
    assert [(option.duid, option.name) for option in projection.options] == [
        (4, "Choir"),
        (9, "Food pantry"),
    ]
    assert projection.memberships == ((3, (4,)),)
    assert inputs(selected_duids=[9]).memberships == ((3, ()),)
    assert inputs(selected_duids=[]).options == ()


def test_roles_reduce_to_current_membership_and_labels_have_stable_order():
    """Multiple roles and equal casefolded names have deterministic results."""
    projection = inputs(
        catalog=[
            {"id": 4, "name": "choir", "catalog_present": True},
            {"id": 9, "name": " Choir ", "catalog_present": True},
        ],
        roster=[
            {"member_key": "3", "ministry_key": "4", "current": True},
            {"member_key": "3", "ministry_key": "4", "current": True},
            {"member_key": "3", "ministry_key": "9", "current": False},
        ],
    )
    assert [option.duid for option in projection.options] == [4, 9]
    assert projection.memberships == ((3, (4,)),)
    assert projection.comparison() == (((4, "choir"), (9, "Choir")), ((3, (4,)),))


def test_hidden_unrepresentable_labels_do_not_invalidate_visible_projection():
    """Irrelevant hidden content cannot block or leak through the visible form."""
    projection = inputs(
        catalog=[
            {"id": 4, "name": "Choir", "catalog_present": True},
            {"id": 10, "name": {"private": "not displayed"}, "catalog_present": True},
        ]
    )
    assert [option.duid for option in projection.options] == [4]


@pytest.mark.parametrize(
    "override",
    [
        {"catalog": None},
        {"catalog": [{"id": True, "name": "Choir", "catalog_present": True}]},
        {"catalog": [{"id": 4, "name": "Choir", "catalog_present": False}]},
        {"catalog": [{"id": 4, "name": "", "catalog_present": True}]},
        {"catalog": [{"id": 4, "name": "x" * 513, "catalog_present": True}]},
        {"catalog": [{"id": 4, "name": "bad\x00label", "catalog_present": True}]},
        {"member_duids": (True,)},
        {"member_duids": (3, 3)},
        {"selected_duids": [4, 4]},
        {"selected_duids": [True]},
        {"organization_id": True},
        {"roster": [{"member_key": "03", "ministry_key": "4", "current": True}]},
        {"roster": [{"member_key": "8", "ministry_key": "4", "current": True}]},
        {"roster": [{"member_key": "3", "ministry_key": "4", "current": 1}]},
        {"roster": [{"member_key": "3", "ministry_key": "0", "current": True}]},
        {"roster": [{"member_key": "3", "ministry_key": "４", "current": True}]},
    ],
)
def test_malformed_or_foreign_source_is_rejected_without_values(override):
    """Source failures carry neither raw values nor foreign identifiers."""
    with pytest.raises(InvalidMinistrySource) as error:
        inputs(**override)
    assert str(error.value) == "The Ministry form inputs are unavailable."


def test_complete_choices_allow_join_and_leave_without_roster_mutation():
    """Validation returns normalized intent without changing source or input."""
    value = answer()
    value["members"]["3"] = {"join": [9], "leave": [4]}
    before = deepcopy(value)
    assert validate(value) == before
    assert value == before and inputs().memberships == ((3, (4,)),)


@pytest.mark.parametrize(
    "choice", [[4], [10], [20], [9, 9], [True], ["9"], [[9]], None, {}]
)
def test_join_rejects_current_hidden_duplicate_and_malformed_choices(choice):
    """Only exact offered nonmember Ministry identities can request a join."""
    value = answer()
    value["members"]["3"]["join"] = choice
    with pytest.raises(InvalidMinistryAnswers) as error:
        validate(value)
    assert set(error.value.fields) == {"ministries.members.3.join"}


def test_leave_requires_current_participation():
    """A forged leave cannot target a Ministry outside the current roster."""
    value = answer()
    value["members"]["3"]["leave"] = [9]
    with pytest.raises(InvalidMinistryAnswers) as error:
        validate(value)
    assert set(error.value.fields) == {"ministries.members.3.leave"}


def test_proposed_members_can_only_join_and_terminal_members_have_no_controls():
    """Local identities never claim roster history; terminal controls are absent."""
    identifier = str(uuid4())
    value = {"members": {}, "proposed_members": {identifier: {"join": [9, 4]}}}
    normalized = validate(value, terminal=["3"], proposed=[identifier])
    assert normalized["proposed_members"][identifier]["join"] == [4, 9]
    with pytest.raises(InvalidMinistryAnswers):
        validate(answer(), terminal=["3"])
    value["proposed_members"][identifier]["leave"] = []
    with pytest.raises(InvalidMinistryAnswers):
        validate(value, terminal=["3"], proposed=[identifier])


@pytest.mark.parametrize(
    "mutation", ["foreign_member", "missing_member", "foreign_uuid", "extra_field"]
)
def test_complete_identity_shape_is_closed_and_errors_do_not_echo_forged_keys(mutation):
    """Reject unknown or omitted identities without reflecting attacker text."""
    value = answer()
    if mutation == "foreign_member":
        value["members"]["private-forged-identity"] = {"join": [], "leave": []}
    elif mutation == "missing_member":
        value["members"].clear()
    elif mutation == "foreign_uuid":
        value["proposed_members"]["private-forged-identity"] = {"join": []}
    else:
        value["members"]["3"]["private-forged-identity"] = True
    with pytest.raises(InvalidMinistryAnswers) as error:
        validate(value)
    assert "private-forged-identity" not in str(error.value.fields)


def test_disabled_ministry_module_rejects_all_answers_except_empty_object():
    """An unenabled module has no writable request surface."""
    assert (
        validate_ministry_answers(
            {}, None, terminal_members=frozenset(), proposed_members=frozenset()
        )
        == {}
    )
    for value in (answer(), [], None):
        with pytest.raises(InvalidMinistryAnswers):
            validate_ministry_answers(
                value, None, terminal_members=frozenset(), proposed_members=frozenset()
            )
