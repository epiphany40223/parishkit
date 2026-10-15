"""``integration key status`` and Finish switching: pure tests.

The catalog entries, the ledger and usage errors. The commands against a
real database are in
database/test_admin_integration_keys_cli_postgresql.py.
"""

import io
import json

import pytest

from parishkit.stewardship import admin_cli
from parishkit.stewardship.admin_integration_keys import (
    KEY_TARGETS,
    FinishConfirm,
    FinishPreview,
    KeyStatus,
)
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS
from parishkit.stewardship.audit.schemas import Action
from parishkit.stewardship.service_boundaries import ROTATING_TARGETS

EVENT = "admin_cmd_integration_key_finish_switching"


def test_the_targets_are_the_rotating_keys():
    """The parser's spelled-out targets are the pages' rotating keys."""
    assert set(KEY_TARGETS) == set(ROTATING_TARGETS)
    assert admin_cli.INTEGRATION_KEY_TARGETS == KEY_TARGETS


def test_the_catalog_entries():
    """Status is a read any session runs; the preview is full scope and
    records nothing; the confirmation is fresh-gated, prompts and records
    its own described event."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    status = entries["integration key status"]
    preview = entries["integration key finish-switching-preview"]
    finish = entries["integration key finish-switching"]
    assert status["scope"] == "read_only" and status["watch"]
    assert not status["changes_state"] and status["audit_event"] is None
    assert not status["prompts"] and not status["fresh_gated"]
    assert preview["scope"] == "full" and not preview["changes_state"]
    assert preview["audit_event"] is None and not preview["prompts"]
    assert not preview["fresh_gated"] and not preview["expected_version"]
    assert finish["scope"] == "full" and finish["changes_state"]
    assert finish["prompts"] and finish["fresh_gated"]
    assert finish["audit_event"] == EVENT
    assert Action(EVENT) and EVENT in DESCRIPTIONS
    for entry, model in (
        (status, KeyStatus),
        (preview, FinishPreview),
        (finish, FinishConfirm),
    ):
        assert entry["pr"] == 10
        assert entry["result_fields"] == list(model.field_names())
    options = {option["name"]: option for option in status["options"]}
    assert not options["--request-id"]["required"]
    options = {option["name"]: option for option in finish["options"]}
    assert options["--token"]["required"]


def test_no_document_names_a_fingerprint_or_key():
    """The results carry request ids, states and public settings only."""
    for model in (KeyStatus, FinishPreview, FinishConfirm):
        for name in model.field_names():
            assert "fingerprint" not in name and "key" not in name, name


def test_the_ledger_covers_the_status_and_the_switch():
    """Both routes are covered; replacing a key is still owed by PR 10."""
    entry = LEDGER["credential_status"]
    assert entry.kind == "command" and entry.owed is None
    assert entry.detail == ("integration key status",)
    entry = LEDGER["select_credential"]
    assert entry.kind == "command" and entry.owed is None
    assert entry.detail == (
        "integration key finish-switching-preview",
        "integration key finish-switching",
    )


REQUEST = "0b9e1c4e-8a8e-4f43-9a51-3f6c1d2e7a10"


@pytest.mark.parametrize(
    "argv",
    [
        ["integration", "key", "status"],
        ["integration", "key", "status", "backup"],
        ["integration", "key", "status", "email"],
        ["integration", "key", "status", "slack", "--request-id", "not-a-uuid"],
        ["integration", "key", "finish-switching-preview"],
        ["integration", "key", "finish-switching-preview", "backup"],
        ["integration", "key", "finish-switching", "slack"],
        ["integration", "key", "finish-switching", "--token", "x"],
        ["integration", "key", "finish-switching", "email", "--token", "x"],
        ["integration", "key", "replace", "slack"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """No or a keyless integration, a malformed request id, no token, or a
    key replacement (not a command yet)."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"


def test_a_watch_keeps_following_the_first_key_change(monkeypatch):
    """Without --request-id, the first read's key change is pinned, so a
    watch never moves to a newer one that starts meanwhile."""
    from argparse import Namespace
    from uuid import UUID, uuid4

    from parishkit.stewardship import admin_integration_keys

    seen = []

    def read(caller, runtime, target, request_id=None):
        """Answer the latest key change, a new one on every call."""
        seen.append(request_id)
        return KeyStatus(target, str(uuid4()), "staged", True, False)

    monkeypatch.setattr(admin_integration_keys, "read_key_status", read)
    args = Namespace(target="slack", request_id=None)
    first = admin_cli.integration_key_status(args, None, None, {"caller": None})
    admin_cli.integration_key_status(args, None, None, {"caller": None})
    assert seen == [None, UUID(first.request_id)]
    assert args.request_id == UUID(first.request_id)
