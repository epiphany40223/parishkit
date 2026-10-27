"""Campaign settings commands: ``campaign show``, ``preview`` and ``confirm``.

Pure tests: the change document's parser, the catalog entries, the
documents' member names, the ledger and usage errors. The commands against
a real database are in database/test_admin_campaign_cli_postgresql.py.
"""

import io
import json

import pytest

from parishkit.stewardship import admin_campaign, admin_cli
from parishkit.stewardship.admin_changes import InvalidChange
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS

from .test_admin_reads import FORBIDDEN

VERSION = "b" * 64


def test_the_parser_takes_the_pages_form_fields():
    """Any non-empty subset of the form's fields, typed as JSON."""
    fields = {
        "name": "Stewardship 2027",
        "year_label": None,
        "census": True,
        "ministry_duids": [4, "9"],
        "ministry_leader_roles": ["Chairperson"],
    }
    assert admin_campaign.parse_settings(json.dumps({"campaign": fields})) == fields
    names, booleans, multiples = admin_campaign._fields()
    assert "base_digest" not in names and "name" in names
    assert booleans == {
        "census",
        "ministry",
        "financial_enabled",
        "additional_information",
    }
    assert set(multiples) <= set(names)


@pytest.mark.parametrize(
    "text",
    ["", "[]", '{"campaign": {}}', '{"campaign": []}', '{"campaign": {}, "x": 1}'],
)
def test_the_parser_refuses_a_malformed_document(text):
    """Not JSON, not one non-empty campaign object: invalid."""
    with pytest.raises(ValueError):
        admin_campaign.parse_settings(text)


@pytest.mark.parametrize(
    ("member", "field"),
    [
        ({"colour": "red"}, "campaign.colour"),
        ({"base_digest": VERSION}, "campaign.base_digest"),
        ({"census": "yes"}, "campaign.census"),
        ({"ministry_duids": 4}, "campaign.ministry_duids"),
        ({"ministry_duids": [True]}, "campaign.ministry_duids"),
        ({"name": None}, "campaign.name"),
        ({"end_date": 20541115}, "campaign.end_date"),
    ],
)
def test_the_parser_names_each_bad_field(member, field):
    """A field the page has not, or a JSON type it cannot take, at its name."""
    with pytest.raises(InvalidChange) as raised:
        admin_campaign.parse_settings(json.dumps({"campaign": member}))
    assert [item["field"] for item in raised.value.fields] == [field]


@pytest.mark.parametrize("retired", [True, False, None])
def test_the_retired_overlap_confirmation_is_accepted_and_ignored(retired):
    """``campaign show`` printed ``overlap_confirmed`` until #2293 retired
    it, so a script that sends back what ``show`` printed keeps working:
    the member is dropped with no error, as ``weekday`` is (#2102)."""
    fields = {"name": "Stewardship 2055", "overlap_confirmed": retired}
    parsed = admin_campaign.parse_settings(json.dumps({"campaign": fields}))
    assert parsed == {"name": "Stewardship 2055"}
    families = admin_campaign.parse_settings(
        json.dumps({"campaign": fields}), families=True
    )
    assert families == {"name": "Stewardship 2055"}


def test_the_retired_overlap_confirmation_still_takes_only_a_boolean():
    """Any other value is refused at its name (``invalid``)."""
    with pytest.raises(InvalidChange) as raised:
        admin_campaign.parse_settings(
            json.dumps({"campaign": {"overlap_confirmed": "yes"}})
        )
    assert raised.value.fields == [
        {
            "field": "campaign.overlap_confirmed",
            "code": "invalid",
            "message": "Give true, false or null.",
        }
    ]


def test_the_catalog_entries():
    """The read: any session, nothing recorded. Review and Apply: full scope,
    no prompt or recent sign-in; only the Apply records its own event."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    show = entries["campaign show"]
    preview = entries["campaign preview"]
    confirm = entries["campaign confirm"]
    assert show["scope"] == "read_only" and not show["changes_state"]
    assert show["audit_event"] is None
    assert show["result_fields"] == list(admin_campaign.CampaignShow.field_names())
    for entry in (show, preview, confirm):
        assert entry["pr"] == 10 and entry["arguments"] == []
        assert not entry["prompts"] and not entry["fresh_gated"]
    for entry in (preview, confirm):
        assert entry["scope"] == "full"
    assert not preview["changes_state"] and preview["expected_version"]
    assert preview["audit_event"] is None
    options = {option["name"]: option for option in preview["options"]}
    assert options["--expected-version"]["required"]
    assert options["--changes"]["required"]
    assert confirm["changes_state"] and not confirm["expected_version"]
    assert confirm["audit_event"] == "admin_cmd_campaign_confirm"
    assert "admin_cmd_campaign_confirm" in DESCRIPTIONS
    options = {option["name"]: option for option in confirm["options"]}
    assert options["--token"]["required"]


def test_the_documents_print_only_allowed_names():
    """Settings, a review and a request: never a Family's identity."""
    assert set(admin_campaign.CampaignPreview.field_names()) == {
        "version",
        "campaign_id",
        "kind",
        "changes",
        "replanned",
        "ministries",
        "notes",
        "preview",
    }
    names = (
        set(admin_campaign.CampaignShow.field_names())
        | set(admin_campaign.CampaignPreview.field_names())
        | set(admin_campaign.CampaignConfirm.field_names())
    )
    for name in names:
        assert FORBIDDEN.search(name) is None, name


def test_the_ledger_covers_the_page():
    """The page is covered by its read, Review and Apply; nothing is owed.
    Create the campaign has its own commands (test_admin_campaign_create)."""
    parity = LEDGER["campaign_settings"]
    assert parity.kind == "command" and parity.owed is None
    assert parity.detail == ("campaign show", "campaign preview", "campaign confirm")


@pytest.mark.parametrize(
    "argv",
    [
        ["campaign", "show", "extra"],
        ["campaign", "preview", "--expected-version", VERSION],
        ["campaign", "preview", "--changes", "{}"],
        ["campaign", "preview", "--changes", "{}", "--expected-version", "nope"],
        ["campaign", "confirm"],
        ["campaign", "confirm", "--token", "-", "extra"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """The read takes nothing of its own; the preview needs its change and
    a version; the confirm needs its token."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
