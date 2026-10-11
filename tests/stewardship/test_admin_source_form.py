"""``report source-form``'s document and catalog entry (ADM-11, #774).

Pure tests: the golden document, built through the projection the command
uses from a stand-in for the page's ``blocked_families``, which carries the
Family name the command leaves out; and the catalog entry. The command
against a real database is in
database/test_admin_source_form_cli_postgresql.py.
"""

import io
import json
from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_source_form
from parishkit.stewardship.admin_parity import LEDGER

from .test_admin_reads import FORBIDDEN, members

CAMPAIGN = UUID("00000000-0000-4000-8000-000000000061")
# DUIDs are what this list is for (the specification's inventory row), so
# they alone are allowed past the personal-data pattern.
DUIDS = {"family_duid", "member_duid"}


def blocked():
    """The page's result: one value finding and one unusable Member record."""
    return {
        "eligible": 12,
        "families": 2,
        "rows": [
            {
                "family_duid": 101,
                "family_name": "Privatename",
                "member_duid": 3,
                "field": "First name",
            },
            {
                "family_duid": 205,
                "family_name": "Unavailable Family",
                "member_duid": None,
                "field": "The Member record itself (an unusable or repeated Member)",
            },
        ],
    }


def test_the_document_is_its_golden_projection():
    """Counts, DUIDs and the page's field words; never the Family name."""
    document = admin_source_form.source_form_model(CAMPAIGN, blocked()).to_document()
    assert document == {
        "campaign_id": str(CAMPAIGN),
        "eligible": 12,
        "families": 2,
        "rows": [
            {"family_duid": 101, "member_duid": 3, "field": "First name"},
            {
                "family_duid": 205,
                "member_duid": None,
                "field": "The Member record itself (an unusable or repeated Member)",
            },
        ],
    }
    for name in set(members(document)) - DUIDS:
        assert FORBIDDEN.search(name) is None, name
    assert "Privatename" not in json.dumps(document)


def test_no_current_campaign_is_an_empty_document():
    """The page's "no campaign" text: null campaign, zero counts, no rows."""
    empty = {"eligible": 0, "families": 0, "rows": []}
    document = admin_source_form.source_form_model(None, empty).to_document()
    assert document == {"campaign_id": None, **empty}


def test_the_catalog_entry():
    """Any session, no state change, the page's event, no prompt or option."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    entry = entries["report source-form"]
    assert entry["scope"] == "read_only" and not entry["changes_state"]
    assert entry["audit_event"] == "source_form_viewed"
    assert not entry["prompts"] and not entry["fresh_gated"]
    names = {option["name"] for option in entry["options"]}
    assert names == {"--config", "--session-stdin"} and entry["arguments"] == []
    assert entry["result_fields"] == list(
        admin_source_form.SourceFormReport.field_names()
    )
    assert LEDGER["source_form"].kind == "command"
    assert LEDGER["source_form"].detail == ("report source-form",)


@pytest.mark.parametrize(
    "argv", [["report", "source-form", "extra"], ["report", "source-form", "--x"]]
)
def test_malformed_commands_are_usage_errors(argv):
    """The command takes no argument and no option."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
