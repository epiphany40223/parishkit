"""``parish preview`` and ``parish confirm``: pure tests.

The change document's shape, the catalog entries, the ledger and usage
errors. The commands against a real database are in
database/test_admin_parish_cli_postgresql.py.
"""

import io
import json

import pytest

from parishkit.stewardship import admin_cli
from parishkit.stewardship.admin_changes import InvalidChange
from parishkit.stewardship.admin_parish import (
    CHANGES_LIMIT,
    ParishConfirm,
    ParishPreview,
    parse_profile,
)
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS


def test_a_change_document_names_profile_fields():
    """Any non-empty subset of the profile; null is a blank value."""
    fields = parse_profile(
        json.dumps({"parish": {"phone": "(212) 555-1234", "online_giving_url": None}})
    )
    assert fields == {"phone": "(212) 555-1234", "online_giving_url": None}


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        json.dumps({"parish": {}}),
        json.dumps({"parish": []}),
        json.dumps({"parish": {"name": "A"}, "extra": 1}),
        json.dumps({"parish": {"name": 3}}),
        json.dumps({"parish": {"name": ["A"]}}),
        "x" * (CHANGES_LIMIT + 1),
    ],
)
def test_a_malformed_change_document_is_invalid(text):
    """Anything but ``{"parish": {field: string or null}}`` is refused."""
    with pytest.raises(ValueError):
        parse_profile(text)


def test_an_unknown_field_is_named():
    """A member that is not a profile field is refused at its own name."""
    with pytest.raises(InvalidChange) as raised:
        parse_profile(json.dumps({"parish": {"name": "A", "logo": "x"}}))
    assert [problem["field"] for problem in raised.value.fields] == ["parish.logo"]
    assert raised.value.fields[0]["code"] == "invalid"


@pytest.mark.parametrize(
    "changes,fields",
    [
        ({"date_format": None}, ["parish.date_format"]),
        ({"date_format": ""}, ["parish.date_format"]),
        ({"name": None, "phone": None}, ["parish.name", "parish.phone"]),
    ],
)
def test_only_the_giving_link_may_be_null(changes, fields):
    """A null (or a blank date format) would silently reset to a default."""
    with pytest.raises(InvalidChange) as raised:
        parse_profile(json.dumps({"parish": changes}))
    assert [problem["field"] for problem in raised.value.fields] == fields


def test_the_catalog_entries():
    """Full scope, as the page's posts; no prompt and no recent sign-in.

    The preview records nothing, as the page's Review; the confirmation
    records its own described event.
    """
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    preview, confirm = entries["parish preview"], entries["parish confirm"]
    for entry in (preview, confirm):
        assert entry["scope"] == "full" and entry["pr"] == 10
        assert not entry["prompts"] and not entry["fresh_gated"]
        assert entry["arguments"] == []
    assert not preview["changes_state"] and preview["expected_version"]
    assert preview["audit_event"] is None
    assert preview["result_fields"] == list(ParishPreview.field_names())
    options = {option["name"]: option for option in preview["options"]}
    assert options["--expected-version"]["required"]
    assert options["--changes"]["required"]
    assert confirm["changes_state"]
    assert confirm["audit_event"] == "admin_cmd_parish_confirm"
    assert "admin_cmd_parish_confirm" in DESCRIPTIONS
    assert confirm["result_fields"] == list(ParishConfirm.field_names())
    options = {option["name"]: option for option in confirm["options"]}
    assert options["--token"]["required"]


def test_the_ledger_covers_parish_settings():
    """Parish settings is covered: its read, Review and Apply."""
    entry = LEDGER["parish_settings"]
    assert entry.kind == "command" and entry.owed is None
    assert entry.detail == ("parish show", "parish preview", "parish confirm")


@pytest.mark.parametrize(
    "argv",
    [
        ["parish", "preview", "--changes", "{}"],
        ["parish", "preview", "--expected-version", "abc", "--changes", "{}"],
        ["parish", "preview", "--expected-version", "a" * 64],
        ["parish", "confirm"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """A missing or malformed version, change document or token."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
