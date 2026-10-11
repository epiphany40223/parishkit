"""Member "cannot participate in any ministries" and retired talents (#247, #970)."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import campaign_values
from parishkit.stewardship.responses.financial import ShareOption
from parishkit.stewardship.responses.inputs import definition_digest
from parishkit.stewardship.responses.ministry import MinistryInputs, MinistryOption
from parishkit.stewardship.responses.presentation import _service_presentation
from parishkit.stewardship.responses.service import (
    DEFAULT_TALENTS,
    InvalidServiceAnswers,
    configured_talent_options,
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
    """A complete service payload as the Family form sends it (no talents)."""
    result = {
        "members": {
            "1": {"cannot_serve": False},
            "2": {"cannot_serve": False},
        },
        "proposed_members": {},
    }
    result["members"].update(entries)
    return result


def test_campaign_without_a_list_hashes_fixed_default_talents():
    """The digest still resolves the defaults, so issued forms still verify."""
    configuration = {"modules": ["ministry"]}
    assert configured_talent_options(configuration) == DEFAULT_TALENTS
    assert [option.label for option in DEFAULT_TALENTS] == [
        "Painter",
        "Florist",
        "Seamstress",
        "Carpenter",
        "Attorney",
        "Gardener",
        "Other",
    ]
    assert [option.free_text for option in DEFAULT_TALENTS] == [False] * 6 + [True]
    assert configured_talent_options({"modules": ["census"]}) == ()


def test_edited_talent_list_replaces_the_defaults():
    """An Admin-edited list is the campaign's own, in its own order."""
    identifier = str(uuid4())
    configuration = {
        "modules": ["ministry"],
        "talent_options": [{"id": identifier, "label": "Musician", "free_text": False}],
    }
    assert configured_talent_options(configuration) == (
        ShareOption(identifier, "Musician", False),
    )


def test_talent_list_still_shapes_the_form_definition():
    """A stored talent list keeps its digest (#970), so issued forms verify."""
    base = campaign(modules=["ministry"], ministry_duids=[7])["values"]
    edited = base | {
        "talent_options": [
            {"id": str(uuid4()), "label": "Musician", "free_text": False}
        ]
    }
    assert definition_digest(base) != definition_digest(edited)
    assert definition_digest(base) != definition_digest(base | {"talent_options": []})


# A fixed configuration whose definition digest is pinned below.
PINNED = {
    "modules": ["ministry"],
    "ministry_duids": [7, 8],
    "content_versions": {"welcome": 1, "ministry": 2},
    "name": "Stewardship 2027",
    "start_date": "2026-10-03",
    "end_date": "2026-11-15",
    "timezone": "America/New_York",
    "additional_information": False,
}


def test_definition_digest_is_pinned_across_the_talent_retirement():
    """The digests equal those computed before #970 for the same configuration.

    A Family's open form holds its definition digest; if a later change to
    configured_talent_options (or the digest) altered it for an unchanged
    configuration, that Family's submission would be refused as
    unverifiable. Change these values only with a deliberate form-definition
    change.
    """
    assert definition_digest(PINNED) == (
        "fa0c1d0de638b21f2475290c888fcc9207965feb63419e4b0395f17957944b8d"
    )
    edited = PINNED | {
        "talent_options": [
            {
                "id": "7f000000-0000-4000-8000-000000000001",
                "label": "Musician",
                "free_text": False,
            }
        ]
    }
    assert definition_digest(edited) == (
        "a57514d5dd9e2f402ccb2919c38d8cbe32b4156c5956f0c80c050735bea13b63"
    )


def test_campaign_configuration_accepts_an_optional_talent_list():
    """Older configurations hold one (#970); a stored list still validates."""
    values = campaign(modules=["ministry"], ministry_duids=[7])["values"]
    campaign_values(values)
    stored = [
        {"id": option.id, "label": option.label, "free_text": option.free_text}
        for option in DEFAULT_TALENTS
    ]
    campaign_values(values | {"talent_options": stored})
    for bad in (
        [{"id": "not-a-uuid", "label": "Painter", "free_text": False}],
        [{"id": str(uuid4()), "label": "Painter"}],
        [{"id": PAINTER, "label": "A", "free_text": False}] * 2,
        "Painter",
    ):
        with pytest.raises(ConfigError):
            campaign_values(values | {"talent_options": bad})


def test_cannot_serve_requires_stopping_every_current_ministry():
    """The lock leaves every current Ministry and joins none."""
    locked = {"1": {"cannot_serve": True}}
    result = validate_service_answers(
        service(**locked),
        ministries(**{"1": {"join": [], "leave": [7, 8]}}),
        INPUTS,
    )
    assert result["members"]["1"]["cannot_serve"] is True
    for choices in ({"join": [], "leave": [7]}, {"join": [9], "leave": [7, 8]}):
        with pytest.raises(InvalidServiceAnswers) as caught:
            validate_service_answers(
                service(**locked), ministries(**{"1": choices}), INPUTS
            )
        assert set(caught.value.fields) == {"service.members.1"}


def test_cannot_serve_with_no_current_ministries_only_forbids_joining():
    """A Member with no roster simply joins nothing."""
    locked = {"2": {"cannot_serve": True}}
    validate_service_answers(service(**locked), ministries(), INPUTS)
    with pytest.raises(InvalidServiceAnswers):
        validate_service_answers(
            service(**locked),
            ministries(**{"2": {"join": [7], "leave": []}}),
            INPUTS,
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
        {"members": {"1": {"talents": {}}}, "proposed_members": {}},
        {
            "members": {"1": {"cannot_serve": False, "talents": [PAINTER]}},
            "proposed_members": {},
        },
        {"members": {"1": {}}, "proposed_members": {}},
        {
            "members": {"1": {"cannot_serve": False, "other": 1}},
            "proposed_members": {},
        },
    ],
)
def test_service_names_only_known_members_with_exact_fields(payload):
    """No Member may be invented, and each entry holds ``cannot_serve`` and,
    from a form opened before #970, a ``talents`` map; nothing else."""
    with pytest.raises(InvalidServiceAnswers):
        validate_service_answers(payload, ministries(), INPUTS)


def test_omitted_members_default_to_participating():
    """The stored answer always lists every Ministry identity explicitly."""
    expected = {
        "members": {
            "1": {"cannot_serve": False, "talents": {}},
            "2": {"cannot_serve": False, "talents": {}},
        },
        "proposed_members": {},
    }
    for payload in ({}, {"members": {}, "proposed_members": {}}):
        assert validate_service_answers(payload, ministries(), INPUTS) == expected


def test_disabled_ministry_module_accepts_only_an_empty_section():
    """Without the Ministry page there is no "cannot participate" to record."""
    assert validate_service_answers({}, {}, None) == {}
    with pytest.raises(InvalidServiceAnswers):
        validate_service_answers(service(), {}, None)


def test_entries_without_talents_are_stored_with_an_empty_map():
    """The form sends only ``cannot_serve`` (#970); the stored entry keeps the
    empty ``talents`` map the database guard requires."""
    locked = {"1": {"cannot_serve": True}}
    result = validate_service_answers(
        service(**locked),
        ministries(**{"1": {"join": [], "leave": [7, 8]}}),
        INPUTS,
    )
    assert result["members"] == {
        "1": {"cannot_serve": True, "talents": {}},
        "2": {"cannot_serve": False, "talents": {}},
    }


def test_stale_talents_from_an_older_form_are_dropped():
    """A form opened before #970 may still send talents; they are not stored.

    Refusing them would lose that Family's whole response mid-campaign, and
    the database guard accepts only an empty map since migration 0056, so
    any talents map is accepted and stored empty. A value that is not a map
    is refused like any other malformed entry, with only the trusted path.
    """
    for talents in ({PAINTER: ""}, {OTHER: " Organ "}, {"foreign-private-id": "x"}):
        result = validate_service_answers(
            service(**{"1": {"cannot_serve": False, "talents": talents}}),
            ministries(),
            INPUTS,
        )
        assert result["members"]["1"] == {"cannot_serve": False, "talents": {}}
    with pytest.raises(InvalidServiceAnswers) as caught:
        validate_service_answers(
            service(**{"1": {"cannot_serve": False, "talents": "private"}}),
            ministries(),
            INPUTS,
        )
    assert set(caught.value.fields) == {"service.members.1"}
    assert "private" not in str(caught.value.fields)


def test_prior_talents_are_never_prefilled():
    """A saved answer's talents are never shown (#970); the stored row keeps them."""
    prior = SimpleNamespace(
        answers={
            "service": {
                "members": {
                    "1": {"cannot_serve": False, "talents": {PAINTER: "", OTHER: "x"}},
                    "2": {"cannot_serve": True, "talents": {}},
                },
                "proposed_members": {},
            }
        }
    )
    shown = _service_presentation(ministries(), prior)
    # Kept empty for a tab loaded before #970, whose script reads it.
    assert shown["talent_options"] == []
    assert shown["members"] == {
        "1": {"cannot_serve": False},
        "2": {"cannot_serve": True},
    }
