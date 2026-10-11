"""The exact daily export commands' documents (ADM-11 PR 8h).

Pure tests: the golden ``export exact status`` and change documents with
their member allowlists, when a ``--watch`` stops, and the catalog entries.
The commands against a real database are in
database/test_admin_exact_export_cli_postgresql.py.
"""

from uuid import UUID

import pytest

from parishkit.stewardship import admin_cli, admin_exact_exports

from .test_admin_reads import FORBIDDEN, members

EXACT = UUID("00000000-0000-4000-8000-000000000071")
CAMPAIGN = UUID("00000000-0000-4000-8000-000000000072")
EXPORT = UUID("00000000-0000-4000-8000-000000000073")
KEY = UUID("00000000-0000-4000-8000-000000000074")
CREATED = "2054-10-05T14:00:00+00:00"


def status(state, export_id=None, expires_at=None):
    """``exact_export_status``'s result for the request, in ``state``."""
    return {
        "id": str(EXACT),
        "campaign_id": str(CAMPAIGN),
        "report": "participation",
        "format": "csv",
        "population_scope": "historical",
        "created_at": CREATED,
        "state": state,
        "export_id": str(export_id) if export_id else None,
        "expires_at": expires_at,
    }


QUEUED = {
    "id": str(EXACT),
    "campaign_id": str(CAMPAIGN),
    "report": "participation",
    "format": "csv",
    "population_scope": "historical",
    "created_at": CREATED,
    "state": "queued",
    "export_id": None,
    "expires_at": None,
    "changes_available": True,
    "can_cancel": True,
}


def test_the_status_document_is_its_golden_projection():
    """Identifiers, states and instants, and Cancel while still queued."""
    model = admin_exact_exports.exact_model(status("queued"), mutable=True)
    document = model.to_document()
    assert document == QUEUED
    for name in members(document):
        assert FORBIDDEN.search(name) is None, name
    ready = admin_exact_exports.exact_model(
        status("ready", EXPORT, "2054-10-12T14:00:00+00:00"), mutable=True
    ).to_document()
    assert ready["export_id"] == str(EXPORT) and ready["can_cancel"] is False


def test_a_change_document_carries_the_status():
    """``created``, the key and the request's status afterwards."""
    change = admin_exact_exports.ExactChange(
        created=True, request_key=KEY, exact=QUEUED
    ).to_document()
    assert change == {"created": True, "request_key": str(KEY), "exact": QUEUED}


@pytest.mark.parametrize(
    "state,export_id,terminal",
    [
        ("queued", None, False),
        ("running", None, False),
        ("retry_wait", None, False),
        ("succeeded", None, False),
        ("failed", None, True),
        ("cancelled", None, True),
        ("queued", EXPORT, False),
        ("ready", EXPORT, True),
        ("failed", EXPORT, True),
    ],
)
def test_a_watch_stops_once_nothing_is_left_to_wait_for(state, export_id, terminal):
    """Still calculating, or handed off and still rendering, keeps watching."""
    model = admin_exact_exports.exact_model(status(state, export_id), mutable=True)
    assert model.terminal is terminal


def test_the_catalog_lists_the_exact_commands():
    """Status is any session's passive read; the changes need full scope."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    status_entry = entries["export exact status"]
    assert status_entry["scope"] == "read_only" and status_entry["watch"]
    assert status_entry["arguments"] == ["EXACT_ID"]
    for verb in ("create", "cancel", "retry"):
        entry = entries[f"export exact {verb}"]
        assert entry["scope"] == "full" and entry["changes_state"], verb
        assert entry["audit_event"] == f"admin_cmd_export_exact_{verb}", verb
        assert entry["request_key"] == (verb != "cancel"), verb
    create = {o["name"]: o for o in entries["export exact create"]["options"]}
    assert create["--scope"]["choices"] == ["historical", "current"]
    assert create["--format"]["choices"] == ["csv", "png", "pdf", "xlsx"]
