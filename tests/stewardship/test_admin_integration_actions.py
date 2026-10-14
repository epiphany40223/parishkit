"""``integration backup-probe`` and ``integration dismiss``: pure tests.

The catalog entries, the ledger and usage errors. The commands against a
real database are in
database/test_admin_integration_actions_cli_postgresql.py.
"""

import io
import json

import pytest

from parishkit.stewardship import admin_cli
from parishkit.stewardship.admin_integration_actions import BackupProbe, KeyDismissal
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS
from parishkit.stewardship.audit.schemas import Action


def test_the_catalog_entries():
    """Full scope, as the pages' posts; neither fresh-gated nor prompting.

    Each records its own described event.
    """
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    probe, dismiss = entries["integration backup-probe"], entries["integration dismiss"]
    for entry, event, model in (
        (probe, "admin_cmd_integration_backup_probe", BackupProbe),
        (dismiss, "admin_cmd_integration_dismiss", KeyDismissal),
    ):
        assert entry["scope"] == "full" and entry["pr"] == 10
        assert entry["changes_state"]
        assert not entry["prompts"] and not entry["fresh_gated"]
        assert not entry["expected_version"] and not entry["watch"]
        assert entry["audit_event"] == event
        assert Action(event) and event in DESCRIPTIONS
        assert entry["result_fields"] == list(model.field_names())
    options = {option["name"]: option for option in probe["options"]}
    assert not options["--folder"]["required"]
    options = {option["name"]: option for option in dismiss["options"]}
    assert options["--request-id"]["required"]


def test_the_ledger_covers_the_check_and_the_dismissal():
    """Test access is part of the settings page; Dismiss is its own route."""
    assert "integration backup-probe" in LEDGER["integration_settings"].detail
    entry = LEDGER["dismiss_credential_result"]
    assert entry.kind == "command" and entry.owed is None
    assert entry.detail == ("integration dismiss",)


REQUEST = "0b9e1c4e-8a8e-4f43-9a51-3f6c1d2e7a10"


@pytest.mark.parametrize(
    "argv",
    [
        ["integration", "dismiss", "slack"],
        ["integration", "dismiss", "--request-id", REQUEST],
        ["integration", "dismiss", "email", "--request-id", REQUEST],
        ["integration", "dismiss", "backup", "--request-id", REQUEST],
        ["integration", "dismiss", "slack", "--request-id", "not-a-uuid"],
        ["integration", "dismiss", "slack", "--request-id", REQUEST.upper()],
        ["integration", "backup-probe", "parishsoft"],
        ["integration", "backup-probe", "--folder"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """No request, no or a keyless integration, a malformed request id, a
    target for the check (which is always the backup's) or an empty option."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
