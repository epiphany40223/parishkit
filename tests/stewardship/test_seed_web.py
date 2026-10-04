"""The seeder's answer mapping onto the Family form's authorized projection (#476).

No database: a form projection is built by hand with the real input types and
``build_answers`` must produce exactly the payload ``validate_answers`` admits,
with the timeline's abstract answers mapped onto the enabled modules.
"""

from types import SimpleNamespace

from parishkit.stewardship.local.seed_web import SEEDER_ADDRESS, build_answers
from parishkit.stewardship.responses.answers import validate_answers
from parishkit.stewardship.responses.comparison import ValueKind
from parishkit.stewardship.responses.financial import ShareOption
from parishkit.stewardship.responses.inputs import FieldInput
from parishkit.stewardship.responses.member_census import MEMBER_FIELDS
from parishkit.stewardship.responses.merge import KnownValue
from parishkit.stewardship.responses.ministry import MinistryInputs, MinistryOption

MEMBERS = (501, 502)
MINISTRIES = (9001, 9002, 9003)


def member_values(identifier):
    """A realistic source row for one Member, by census field."""
    return {
        "first_name": f"First{identifier}",
        "last_name": "Household",
        "birth_date": "1980-05-04",
        "gender": "Female" if identifier % 2 else "Male",
        "email": f"member{identifier}@example.test",
        "language": "English",
        "marital_status": "Married",
    }


def form(*, modules=("census", "ministry", "financial")):
    """A form projection with the real FieldInput, Ministry and share types."""
    fields = []
    for identifier in MEMBERS:
        values = member_values(identifier)
        for definition in MEMBER_FIELDS:
            fields.append(
                FieldInput(
                    "member",
                    identifier,
                    definition.name,
                    definition.kind,
                    KnownValue(True, values.get(definition.name)),
                )
            )
    for name in ("home_address", "mailing_address"):
        fields.append(
            FieldInput("family", "family", name, ValueKind.ADDRESS, KnownValue(False))
        )
    fields.append(
        FieldInput(
            "family", "family", "email_opt_out", ValueKind.BOOLEAN, KnownValue(False)
        )
    )
    options = (
        ShareOption("share-a", "Online", False),
        ShareOption("share-b", "Other", True),
    )
    inputs = SimpleNamespace(
        modules=tuple(modules),
        member_duids=MEMBERS,
        fields=tuple(fields),
        ministries=MinistryInputs(
            tuple(MinistryOption(duid, f"Ministry {duid}") for duid in MINISTRIES),
            ((MEMBERS[0], (MINISTRIES[2],)), (MEMBERS[1], ())),
        )
        if "ministry" in modules
        else None,
        financial=SimpleNamespace(definition=SimpleNamespace(options=options))
        if "financial" in modules
        else None,
        talent_options=(),
    )
    return SimpleNamespace(inputs=inputs, baseline=SimpleNamespace(pk="baseline"))


ANSWERS = {
    "pledge": 1200,
    "ministry_interest": [9001, 9002],
    "census_edit": "changed_email",
    "email_opt_out": True,
    "information": "We moved last spring.",
}


def test_answers_map_onto_every_enabled_module_and_validate():
    """Pledge, Ministry interest, a changed email, the opt-out and free text."""
    payload = build_answers(form(), ANSWERS)
    assert set(payload) == {
        "family",
        "members",
        "proposed_members",
        "ministries",
        "additional_information",
        "financial",
    }
    assert payload["members"]["501"]["email"] == "family501@example.test"
    assert payload["members"]["502"]["email"] == "member502@example.test"
    assert payload["members"]["501"]["first_name"] == "First501"
    assert payload["family"]["email_opt_out"] is True
    assert payload["ministries"]["members"]["501"] == {
        "join": [9001, 9002],
        "leave": [],
    }
    assert payload["ministries"]["members"]["502"] == {"join": [], "leave": []}
    assert payload["financial"] == {
        "annual_pledge": "1200",
        "frequency": "monthly",
        "shares": {"share-a": ""},
        "cannot_give": False,
    }
    assert payload["additional_information"] == "We moved last spring."
    # The real validator admits it: this is what the form owner would commit.
    from datetime import date

    normalized = validate_answers(
        payload,
        _census_inputs(form()),
        additional_enabled=True,
        today=date(2026, 10, 1),
    )
    assert normalized["financial"]["annual_pledge"] == "1200.00"
    assert normalized["ministries"]["members"]["501"]["join"] == [9001, 9002]


def test_no_answers_give_the_untouched_form():
    payload = build_answers(form(), {})
    assert payload["members"]["501"]["email"] == "member501@example.test"
    assert payload["family"]["email_opt_out"] is None
    assert payload["ministries"]["members"]["501"] == {"join": [], "leave": []}
    assert payload["financial"]["annual_pledge"] == "0"
    assert payload["additional_information"] == ""
    from datetime import date

    validate_answers(
        payload,
        _census_inputs(form()),
        additional_enabled=True,
        today=date(2026, 10, 1),
    )


def test_disabled_modules_get_empty_sections():
    payload = build_answers(form(modules=("census",)), ANSWERS)
    assert "financial" not in payload
    assert payload["ministries"] == {}
    payload = build_answers(form(modules=()), ANSWERS)
    assert payload["family"] == {} and payload["members"] == {"501": {}, "502": {}}


def test_seeder_address_is_loopback():
    assert SEEDER_ADDRESS == "127.0.0.1"


def _census_inputs(projection):
    """The validator's CensusInputs built from the hand-made projection.

    The validator reads only ``definition.options`` from the financial inputs,
    so a stand-in with that shape is enough there; everything else is real.
    """
    from parishkit.stewardship.responses.inputs import CensusInputs

    inputs = projection.inputs
    return CensusInputs(
        family_duid=100001,
        member_duids=inputs.member_duids,
        fields=inputs.fields,
        definition_digest="d" * 64,
        modules=inputs.modules,
        ministries=inputs.ministries,
        financial=inputs.financial,
        parish_name="Synthetic Parish",
        talent_options=(),
    )
