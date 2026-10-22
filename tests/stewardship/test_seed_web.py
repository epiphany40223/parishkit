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
from parishkit.stewardship.responses.member_requests import local_member_id
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
        family_duid=100001,
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


def test_a_proposed_member_is_a_complete_new_household_member():
    """A census edit may propose one new Member, who joins a Ministry (#498)."""
    from datetime import date

    answers = ANSWERS | {"census_edit": "proposed_member"}
    payload = build_answers(form(), answers)
    ((identity, member),) = payload["proposed_members"].items()
    assert local_member_id(identity)
    assert set(member) == {field.name for field in MEMBER_FIELDS}
    assert member["last_name"] == "Household" and member["first_name"]
    # Existing Members are untouched: no changed email this time.
    assert payload["members"]["501"]["email"] == "member501@example.test"
    assert payload["ministries"]["proposed_members"] == {
        identity: {"join": [MINISTRIES[0]]}
    }
    # The same Family proposes the same person on a resubmission.
    assert build_answers(form(), answers)["proposed_members"] == {identity: member}
    normalized = validate_answers(
        payload,
        _census_inputs(form()),
        additional_enabled=True,
        today=date(2026, 10, 1),
    )
    assert set(normalized["proposed_members"]) == {identity}
    assert normalized["ministries"]["proposed_members"][identity]["join"] == [
        MINISTRIES[0]
    ]
    # Without Ministries the new Member is still proposed, with no Ministry entry.
    census_only = build_answers(form(modules=("census",)), answers)
    assert set(census_only["proposed_members"]) == {identity}
    assert census_only["ministries"] == {}
    validate_answers(
        census_only,
        _census_inputs(form(modules=("census",))),
        additional_enabled=True,
        today=date(2026, 10, 1),
    )
    # Without the census module nobody can be proposed.
    assert build_answers(form(modules=()), answers)["proposed_members"] == {}


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
        family_duid=inputs.family_duid,
        member_duids=inputs.member_duids,
        fields=inputs.fields,
        definition_digest="d" * 64,
        modules=inputs.modules,
        ministries=inputs.ministries,
        financial=inputs.financial,
        parish_name="Synthetic Parish",
    )


def test_create_campaign_fields_fill_the_real_form_and_validator():
    """A catalog like the synthetic parish's (25 Ministries by name, 4 funds).

    The seeder fills Create the campaign's own form (#142), Campaign
    settings' form since #1404 PR 3, keeping its default leader roles; the
    form and the campaign validator both admit what it posts.
    """
    from datetime import date

    from parishkit.stewardship.accounts.campaign_forms import CampaignSettingsForm
    from parishkit.stewardship.campaigns import configuration
    from parishkit.stewardship.local.seed_web import campaign_fields

    names = sorted(f"Ministry {n}" for n in range(25))
    roles = ["Chairperson", "Staff"]
    options = {
        # Sorted by display name, as the source catalog sorts them.
        "ministries": [(str(9000 + n), name) for n, name in enumerate(names)],
        "funds": [("303", "Capital"), ("300", "Offertory"), ("301", "Other")],
        "leader_choices": [(role, role) for role in roles],
        "leader_roles": roles,
    }
    initial = {
        "timezone": "America/New_York",
        "ministry_leader_roles": roles,
        "base_digest": "a" * 64,
    }
    blank = CampaignSettingsForm(initial=initial, previous={}, **options)
    data = campaign_fields(blank, (date(2026, 9, 27), date(2026, 10, 27)))
    assert data["ministry_leader_roles"] == roles
    form = CampaignSettingsForm(data, initial=initial, previous={}, **options)
    assert form.is_valid(), form.errors
    values = form.values()
    # The default roles stay the default: the campaign saves none of its own.
    assert "ministry_leader_roles" not in values
    assert values["modules"] == ["census", "financial", "ministry"]
    assert len(values["ministry_duids"]) == 25
    assert values["financial"]["fund_duids"] == [303]
    assert values["financial"]["comparison_fund_duids"] == [300]
    # A Financial campaign starts with the default share options.
    assert values["share_options"]
    # The real validator admits them (it raises ConfigError otherwise).
    configuration.campaign_values(values)
    # The year label is the year Families pledge for, the financial period's
    # year, as Production labels Stewardship 2027 (held in autumn 2026).
    assert values["year_label"] == "2027"
    assert values["financial"]["start"].startswith(values["year_label"])


def test_unattended_wizard_saves_its_pages_in_the_wizards_order(monkeypatch):
    """`wizard` follows setup_wizard.PAGES, ending with the source load (#1353).

    The page services are recorded instead of run, so this pins only the
    order in which the unattended wizard calls them.
    """
    from parishkit.stewardship.accounts import (
        setup_credentials,
        setup_drafts,
        setup_source,
        setup_staging,
    )
    from parishkit.stewardship.accounts.setup_wizard import PAGES
    from parishkit.stewardship.local import seed_web

    calls = []
    status = SimpleNamespace(attempt_id="attempt", version=1)

    def save_section(request, service, attempt_id, *, step, **_):
        """Record a public settings page save."""
        calls.append(step)
        return status

    def stage_credential(request, service, attempt_id, *, target, **_):
        """Record a credential page save."""
        calls.append(target)
        return status, None

    def started(*_, **__):
        """Record the source load."""
        calls.append("source")

    monkeypatch.setattr(setup_staging, "begin_setup", lambda *_: status)
    monkeypatch.setattr(setup_drafts, "save_section", save_section)
    monkeypatch.setattr(
        setup_drafts, "view_draft", lambda *_: SimpleNamespace(status=status)
    )
    monkeypatch.setattr(setup_credentials, "stage_credential", stage_credential)
    monkeypatch.setattr(setup_source, "start_source_load", started)
    monkeypatch.setattr(seed_web, "stage_logo", lambda *_: "bundle")
    assert seed_web.wizard_collect(None, None) == "attempt"
    # Slack is off, so Test Slack is skipped. The tests need only their own
    # inputs (#1578), so the email test, sent in wizard_confirm after the
    # load, proves the same settings; Review onwards follow there too.
    tests = {"mail_test", "slack_test"}
    pages = [page.key for page in PAGES if page.key not in tests]
    assert calls == pages[: pages.index("source") + 1]
