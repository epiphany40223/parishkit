"""``integration preview`` and ``integration confirm``: pure tests.

The change document's shape, the form post it becomes, the prompt, the
catalog entries, the ledger and usage errors. The commands against a real
database are in database/test_admin_integration_changes_cli_postgresql.py.
"""

import io
import json

import pytest
from django.core import signing

from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts.integration_views import SALT
from parishkit.stewardship.admin_changes import InvalidChange
from parishkit.stewardship.admin_integration_changes import (
    CHANGES_LIMIT,
    IntegrationConfirm,
    IntegrationPreview,
    form_data,
    parse_settings,
    prompt_lines,
    setting_names,
)
from parishkit.stewardship.admin_parity import LEDGER
from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS


def document(settings):
    """A change document with ``settings``."""
    return json.dumps({"settings": settings})


@pytest.mark.parametrize(
    ("target", "names"),
    [
        ("parishsoft", ["organization_id"]),
        ("google_workspace", ["delegated_email"]),
        ("email", ["sender", "sender_name", "reply_to"]),
        ("slack", ["channel_id"]),
        ("backup", ["target"]),
    ],
)
def test_each_target_takes_the_pages_fields(target, names):
    """The page's fields, never its version, schedule rows or a key."""
    assert setting_names(target) == names


def test_a_change_document_names_settings():
    """Any non-empty subset; the From name may be null; numbers are allowed."""
    assert parse_settings(
        document({"sender": "a@example.org", "sender_name": None}), "email"
    ) == {"sender": "a@example.org", "sender_name": None}
    assert parse_settings(document({"organization_id": 7}), "parishsoft") == {
        "organization_id": 7
    }


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        document({}),
        json.dumps({"settings": []}),
        json.dumps({"settings": {"sender": "a@example.org"}, "extra": 1}),
        document({"sender": True}),
        document({"sender": ["a@example.org"]}),
        "x" * (CHANGES_LIMIT + 1),
    ],
)
def test_a_malformed_change_document_is_invalid(text):
    """Anything but ``{"settings": {name: string, number or null}}``."""
    with pytest.raises(ValueError):
        parse_settings(text, "email")


def test_unknown_settings_and_nulls_are_named():
    """Another target's field, a schedule key or a key; a required null."""
    with pytest.raises(InvalidChange) as raised:
        parse_settings(
            document(
                {
                    "sender": None,
                    "channel_id": "C1",
                    "refresh_rules": {},
                    "candidate": "secret",
                }
            ),
            "email",
        )
    assert [(p["field"], p["code"]) for p in raised.value.fields] == [
        ("settings.candidate", "invalid"),
        ("settings.channel_id", "invalid"),
        ("settings.refresh_rules", "invalid"),
        ("settings.sender", "required"),
    ]
    assert "secret" not in json.dumps(raised.value.fields)


def test_the_form_post_keeps_what_the_document_omits():
    """Applied values first, the document's over them; null is blank."""
    stored = {"sender": "a@example.org", "sender_name": "Office", "reply_to": "r@x.org"}
    data = form_data(
        stored,
        setting_names("email"),
        {"sender_name": None, "reply_to": "s@x.org"},
        "f" * 64,
    )
    assert data == {
        "sender": "a@example.org",
        "sender_name": "",
        "reply_to": "s@x.org",
        "base_digest": "f" * 64,
    }
    # No schedule rows are posted, so the page keeps the stored schedule.
    assert form_data({"organization_id": "12"}, ["organization_id"], {}, "0") == {
        "organization_id": "12",
        "base_digest": "0",
    }


def test_the_prompt_names_changed_settings_never_values():
    """Only the settings the reviewed patch changes against the applied ones
    (never an unchanged stored schedule key), or the removal; another
    target's token is refused before the prompt."""
    applied = {"sender": "a@example.org", "reply_to": "r@x", "sender_name": "O"}
    update = [
        {
            "operation": "update",
            "section": "integrations",
            "id": "x",
            "values": {"settings": {**applied, "reply_to": "s@x", "sender_name": None}},
        }
    ]
    update[0]["values"]["settings"].pop("sender_name")
    token = signing.dumps({"patch": update}, salt=SALT + "email")
    [line] = prompt_lines("email", token, applied)
    assert line == (
        "This changes the Outgoing email addresses settings: reply_to, sender_name."
    )
    assert "a@example.org" not in line and "s@x" not in line
    schedule = {"organization_id": "1", "refresh_rules": {"rules": []}}
    patch = [
        {
            "operation": "update",
            "section": "integrations",
            "id": "p",
            "values": {"settings": {**schedule, "organization_id": "2"}},
        }
    ]
    token = signing.dumps({"patch": patch}, salt=SALT + "parishsoft")
    assert prompt_lines("parishsoft", token, schedule) == [
        "This changes the ParishSoft settings: organization_id."
    ]
    # Nothing differs any more (applied since): the plain wording.
    assert prompt_lines("parishsoft", token, patch[0]["values"]["settings"]) == [
        "This applies the reviewed ParishSoft settings."
    ]
    removal = [{"operation": "remove", "section": "integrations", "id": "x"}]
    token = signing.dumps({"patch": removal}, salt=SALT + "slack")
    assert prompt_lines("slack", token, {}) == ["This removes Slack notifications."]
    with pytest.raises(ValueError):
        prompt_lines("email", token, {})


def test_the_catalog_entries():
    """Full scope, as the page's posts; Apply is fresh-gated and prompts.

    The preview records nothing, as the page's review; the confirmation
    records its own described event.
    """
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    preview, confirm = entries["integration preview"], entries["integration confirm"]
    for entry in (preview, confirm):
        assert entry["scope"] == "full" and entry["pr"] == 10
    assert not preview["changes_state"] and preview["expected_version"]
    assert not preview["prompts"] and not preview["fresh_gated"]
    assert preview["audit_event"] is None
    assert preview["result_fields"] == list(IntegrationPreview.field_names())
    options = {option["name"]: option for option in preview["options"]}
    assert options["--expected-version"]["required"]
    assert {"--changes", "--remove"} <= set(options)
    assert confirm["changes_state"] and confirm["prompts"] and confirm["fresh_gated"]
    assert confirm["audit_event"] == "admin_cmd_integration_confirm"
    assert "admin_cmd_integration_confirm" in DESCRIPTIONS
    assert confirm["result_fields"] == list(IntegrationConfirm.field_names())
    options = {option["name"]: option for option in confirm["options"]}
    assert options["--token"]["required"]


def test_the_ledger_covers_the_settings_pages_save_and_remove():
    """The settings page's read, review and Apply; key steps still owed."""
    entry = LEDGER["integration_settings"]
    assert entry.kind == "command" and entry.owed == "PR 10"
    assert entry.detail == (
        "integration show",
        "integration preview",
        "integration confirm",
    )


VERSION = ["--expected-version", "a" * 64]


@pytest.mark.parametrize(
    "argv",
    [
        ["integration", "preview", "email", *VERSION],
        ["integration", "preview", "email", *VERSION, "--changes", "{}", "--remove"],
        ["integration", "preview", "email", "--changes", "{}"],
        ["integration", "preview", "backup_key", *VERSION, "--remove"],
        ["integration", "preview", *VERSION, "--remove"],
        ["integration", "confirm", "email"],
        ["integration", "confirm", "--token", "x"],
        ["integration", "confirm", "email", "--token", "-"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """Neither or both of a change and a removal, no version, an unknown
    target, no token, or a token read from standard input without --yes."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
