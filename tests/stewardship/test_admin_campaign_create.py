"""Create the campaign commands: ``campaign create-preview`` and ``create``.

Pure tests: the catalog entries, the described event, the documents' member
names, the ledger and usage errors. The commands against a real database are
in database/test_admin_campaign_create_cli_postgresql.py.
"""

import io
import json

import pytest

from parishkit.stewardship import admin_campaign_create, admin_cli
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS

from .test_admin_reads import FORBIDDEN

VERSION = "b" * 64


def test_the_catalog_entries():
    """Review and Apply: full scope, no prompt or privilege; only the Apply
    records its own event."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    preview = entries["campaign create-preview"]
    create = entries["campaign create"]
    for entry in (preview, create):
        assert entry["pr"] == 10 and entry["arguments"] == []
        assert entry["scope"] == "full"
        assert not entry["prompts"] and not entry["fresh_gated"]
        assert not entry["request_key"]
    assert not preview["changes_state"] and preview["expected_version"]
    assert preview["audit_event"] is None
    assert preview["result_fields"] == [
        "version",
        "settings",
        "content",
        "preview",
    ]
    options = {option["name"]: option for option in preview["options"]}
    assert options["--expected-version"]["required"]
    assert options["--changes"]["required"]
    assert create["changes_state"] and not create["expected_version"]
    assert create["audit_event"] == "admin_cmd_campaign_create"
    assert create["result_fields"] == ["created", "request"]
    assert "admin_cmd_campaign_create" in DESCRIPTIONS
    options = {option["name"]: option for option in create["options"]}
    assert options["--token"]["required"]


def test_the_documents_print_only_allowed_names():
    """Settings, a review and a request: never a Family's identity."""
    names = set(admin_campaign_create.CampaignCreatePreview.field_names()) | set(
        admin_campaign_create.CampaignCreate.field_names()
    )
    for name in names:
        assert FORBIDDEN.search(name) is None, name


def test_the_ledger_covers_the_page_and_defers_copying():
    """Create the campaign is covered by its Review and Apply; Copy campaign
    waits on #145, which retires or restores it."""
    parity = LEDGER["campaign_create"]
    assert parity.kind == "command" and parity.owed is None
    assert parity.detail == ("campaign create-preview", "campaign create")
    clone = LEDGER["campaign_clone"]
    assert clone.kind == "deferred" and "#145" in clone.detail[0]


def test_a_refused_creation_is_not_available():
    """The page's 409 is not a stale version on the command line: no newer
    version admits a second campaign."""
    from parishkit.stewardship.admin_reads import NotAvailable

    assert isinstance(admin_campaign_create._refused(), NotAvailable)


@pytest.mark.parametrize(
    "argv",
    [
        ["campaign", "create-preview", "--expected-version", VERSION],
        ["campaign", "create-preview", "--changes", "{}"],
        ["campaign", "create-preview", "--changes", "{}", "--expected-version", "x"],
        ["campaign", "create"],
        ["campaign", "create", "--token", "-", "extra"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """The preview needs its document and a version; the create its token."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
