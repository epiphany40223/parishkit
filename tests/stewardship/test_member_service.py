"""Member talents and "cannot participate in any ministries" (issue #247)."""

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import campaign_values
from parishkit.stewardship.responses.financial import ShareOption
from parishkit.stewardship.responses.inputs import definition_digest
from parishkit.stewardship.responses.ministry import MinistryInputs, MinistryOption
from parishkit.stewardship.responses.service import (
    DEFAULT_TALENTS,
    InvalidServiceAnswers,
    default_talent_options,
    talent_options,
    validate_service_answers,
)

from .campaign_factory import campaign

PAINTER, OTHER = DEFAULT_TALENTS[0].id, DEFAULT_TALENTS[-1].id
INPUTS = MinistryInputs(
    (
        MinistryOption(7, "Choir"),
        MinistryOption(8, "Ushers"),
        MinistryOption(9, "Lectors"),
    ),
    ((1, (7, 8)), (2, ())),
)


def ministries(**entries):
    """A validated Ministry answer for Members 1 and 2 (no proposed Members)."""
    result = {
        "members": {"1": {"join": [], "leave": []}, "2": {"join": [], "leave": []}},
        "proposed_members": {},
    }
    result["members"].update(entries)
    return result


def service(**entries):
    """A complete service payload; untouched Members serve with no talents."""
    result = {
        "members": {
            "1": {"cannot_serve": False, "talents": {}},
            "2": {"cannot_serve": False, "talents": {}},
        },
        "proposed_members": {},
    }
    result["members"].update(entries)
    return result


def test_campaign_without_a_list_uses_fixed_default_talents():
    """Defaults resolve identically on every read, so answers stay comparable."""
    configuration = {"modules": ["ministry"]}
    assert talent_options(configuration) == DEFAULT_TALENTS
    assert [option["label"] for option in default_talent_options()] == [
        "Painter",
        "Florist",
        "Seamstress",
        "Carpenter",
        "Attorney",
        "Gardener",
        "Other",
    ]
    assert [option.free_text for option in DEFAULT_TALENTS] == [False] * 6 + [True]
    assert talent_options({"modules": ["census"]}) == ()


def test_edited_talent_list_replaces_the_defaults():
    """An Admin-edited list is the campaign's own, in its own order."""
    identifier = str(uuid4())
    configuration = {
        "modules": ["ministry"],
        "talent_options": [{"id": identifier, "label": "Musician", "free_text": False}],
    }
    assert talent_options(configuration) == (
        ShareOption(identifier, "Musician", False),
    )


def test_talent_list_changes_the_form_definition():
    """Editing talents must refresh open forms, like editing share methods."""
    base = campaign(modules=["ministry"], ministry_duids=[7])["values"]
    edited = base | {
        "talent_options": [
            {"id": str(uuid4()), "label": "Musician", "free_text": False}
        ]
    }
    assert definition_digest(base) != definition_digest(edited)


def test_campaign_configuration_accepts_an_optional_talent_list():
    """Older configurations omit it; a supplied list is validated like shares."""
    values = campaign(modules=["ministry"], ministry_duids=[7])["values"]
    campaign_values(values)
    campaign_values(values | {"talent_options": default_talent_options()})
    for bad in (
        [{"id": "not-a-uuid", "label": "Painter", "free_text": False}],
        [{"id": str(uuid4()), "label": "Painter"}],
        [{"id": PAINTER, "label": "A", "free_text": False}] * 2,
        "Painter",
    ):
        with pytest.raises(ConfigError):
            campaign_values(values | {"talent_options": bad})


def test_talents_normalize_like_share_methods():
    """Plain talents carry no text; "Other" requires its trimmed text."""
    result = validate_service_answers(
        service(
            **{"1": {"cannot_serve": False, "talents": {PAINTER: "", OTHER: " Organ "}}}
        ),
        ministries(),
        INPUTS,
        DEFAULT_TALENTS,
    )
    assert result["members"]["1"] == {
        "cannot_serve": False,
        "talents": {PAINTER: "", OTHER: "Organ"},
    }


@pytest.mark.parametrize(
    "talents",
    [
        {PAINTER: "notes"},
        {OTHER: "  "},
        {OTHER: "x" * 201},
        {"foreign-private-id": ""},
        [PAINTER],
    ],
)
def test_invalid_talents_report_only_trusted_paths(talents):
    """Errors never echo the submitted identity or text."""
    with pytest.raises(InvalidServiceAnswers) as caught:
        validate_service_answers(
            service(**{"1": {"cannot_serve": False, "talents": talents}}),
            ministries(),
            INPUTS,
            DEFAULT_TALENTS,
        )
    assert set(caught.value.fields) == {"service.members.1.talents"}
    assert "private" not in str(caught.value.fields)


def test_cannot_serve_requires_stopping_every_current_ministry():
    """The lock leaves every current Ministry and joins none."""
    locked = {"1": {"cannot_serve": True, "talents": {}}}
    result = validate_service_answers(
        service(**locked),
        ministries(**{"1": {"join": [], "leave": [7, 8]}}),
        INPUTS,
        DEFAULT_TALENTS,
    )
    assert result["members"]["1"]["cannot_serve"] is True
    for choices in ({"join": [], "leave": [7]}, {"join": [9], "leave": [7, 8]}):
        with pytest.raises(InvalidServiceAnswers) as caught:
            validate_service_answers(
                service(**locked), ministries(**{"1": choices}), INPUTS, DEFAULT_TALENTS
            )
        assert set(caught.value.fields) == {"service.members.1"}


def test_cannot_serve_with_no_current_ministries_only_forbids_joining():
    """A Member with no roster simply joins nothing."""
    locked = {"2": {"cannot_serve": True, "talents": {}}}
    validate_service_answers(service(**locked), ministries(), INPUTS, DEFAULT_TALENTS)
    with pytest.raises(InvalidServiceAnswers):
        validate_service_answers(
            service(**locked),
            ministries(**{"2": {"join": [7], "leave": []}}),
            INPUTS,
            DEFAULT_TALENTS,
        )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"members": {}},
        {
            "members": {"99": {"cannot_serve": False, "talents": {}}},
            "proposed_members": {},
        },
        {
            "members": {"1": {"cannot_serve": "no", "talents": {}}},
            "proposed_members": {},
        },
    ],
)
def test_service_names_only_known_members_with_exact_fields(payload):
    """No Member may be invented, and each entry has exactly its two fields."""
    with pytest.raises(InvalidServiceAnswers):
        validate_service_answers(payload, ministries(), INPUTS, DEFAULT_TALENTS)


def test_omitted_members_default_to_no_talents_and_participating():
    """The stored answer always lists every Ministry identity explicitly."""
    expected = {
        "members": {
            "1": {"cannot_serve": False, "talents": {}},
            "2": {"cannot_serve": False, "talents": {}},
        },
        "proposed_members": {},
    }
    for payload in ({}, {"members": {}, "proposed_members": {}}):
        assert (
            validate_service_answers(payload, ministries(), INPUTS, DEFAULT_TALENTS)
            == expected
        )


def test_disabled_ministry_module_accepts_only_an_empty_section():
    """Without the Ministry page there are no talents to record."""
    assert validate_service_answers({}, {}, None, ()) == {}
    with pytest.raises(InvalidServiceAnswers):
        validate_service_answers(service(), {}, None, ())
