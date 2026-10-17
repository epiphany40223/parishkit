"""The Backup encryption key commands: pure tests.

The catalog entries, the described event, the lines after the preamble,
usage errors and the role check. The commands against a real database are
in database/test_admin_backup_key_cli_postgresql.py.
"""

import base64
import io
import json
from argparse import Namespace

import pytest

from parishkit.stewardship import admin_backup_key, admin_changes, admin_cli
from parishkit.stewardship.accounts import backup_key
from parishkit.stewardship.admin_backup_key import (
    FIELD_LIMIT,
    BackupKeyChallenge,
    BackupKeyConfirm,
    BackupKeyPreview,
    line_text,
)
from parishkit.stewardship.admin_changes import InvalidChange
from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS
from parishkit.stewardship.audit.schemas import Action

EVENT = "admin_cmd_integration_backup_key_confirm"


def _entries():
    """The catalog's entries by name."""
    return {entry["name"]: entry for entry in admin_cli.catalog()}


def test_the_catalog_entries():
    """The challenge and preview are full scope and record nothing; the
    confirmation is fresh-gated, prompts and records its described event."""
    entries = _entries()
    challenge = entries["integration backup-key challenge"]
    preview = entries["integration backup-key preview"]
    confirm = entries["integration backup-key confirm"]
    for entry in (challenge, preview):
        assert entry["scope"] == "full" and not entry["changes_state"]
        assert entry["audit_event"] is None and not entry["prompts"]
        assert not entry["fresh_gated"] and not entry["expected_version"]
    assert confirm["scope"] == "full" and confirm["changes_state"]
    assert confirm["prompts"] and confirm["fresh_gated"]
    assert confirm["audit_event"] == EVENT
    assert Action(EVENT) and EVENT in DESCRIPTIONS
    for entry, model in (
        (challenge, BackupKeyChallenge),
        (preview, BackupKeyPreview),
        (confirm, BackupKeyConfirm),
    ):
        assert entry["pr"] == 10
        assert entry["result_fields"] == list(model.field_names())
        assert entry["arguments"] == []


def test_no_option_takes_the_key_or_the_code():
    """The public key and the code come only as the line after the
    preamble; the options only say so."""
    entries = _entries()
    options = {
        name: {option["name"]: option for option in entries[name]["options"]}
        for name in entries
        if name.startswith("integration backup-key ")
    }
    challenge = options["integration backup-key challenge"]
    preview = options["integration backup-key preview"]
    confirm = options["integration backup-key confirm"]
    assert challenge["--public-key-stdin"]["required"]
    assert not challenge["--public-key-stdin"]["takes_value"]
    assert preview["--code-stdin"]["required"]
    assert not preview["--code-stdin"]["takes_value"]
    assert preview["--intent"]["required"] and confirm["--token"]["required"]
    every = set(challenge) | set(preview) | set(confirm)
    assert not {"--public-key", "--public-key-file", "--code", "--code-file"} & every


def test_the_limit_is_the_pages():
    """The command bounds both lines by the page's own field limit."""
    assert FIELD_LIMIT == backup_key.MAX_FIELD


def test_no_document_names_a_key_or_a_code():
    """The documents print fingerprints, the sealed challenge, the signed
    intent and token, never the pasted key or the typed code."""
    for model in (BackupKeyChallenge, BackupKeyPreview, BackupKeyConfirm):
        for name in model.field_names():
            assert name not in {"public_key", "key", "code", "private_key"}, name


@pytest.mark.parametrize(
    ("tag", "value"),
    [
        (admin_cli.PUBLIC_KEY_TAG, b"  AAAA=\n"),
        (admin_cli.PROOF_TAG, b"ABCD-EFGH-JKMN"),
    ],
)
def test_each_tagged_line_is_decoded(tag, value):
    """The public key and code lines give back the exact bytes."""
    stream = io.BytesIO(f"{tag} ".encode() + base64.b64encode(value) + b"\n")
    assert admin_cli.read_secret(stream, tag=tag, size=FIELD_LIMIT) == value


@pytest.mark.parametrize(
    "stream",
    [
        b"",
        b"pk-admin-secret/1 c2VjcmV0\n",
        b"pk-admin-proof/1 c2VjcmV0\n",
        b"pk-admin-public-key/1 c2VjcmV0",
        b"pk-admin-public-key/1 not base64!\n",
        b"pk-admin-public-key/1 " + b"A" * 400 + b"\n",
    ],
)
def test_a_malformed_public_key_line_is_a_usage_error_naming_nothing(stream):
    """Missing, another tag, unterminated, not base64 or over the limit."""
    with pytest.raises(admin_cli.UsageError) as raised:
        admin_cli.read_secret(
            io.BytesIO(stream), tag=admin_cli.PUBLIC_KEY_TAG, size=FIELD_LIMIT
        )
    assert "c2VjcmV0" not in str(raised.value) and "AAAA" not in str(raised.value)


@pytest.mark.parametrize("field", ["public_key", "code"])
@pytest.mark.parametrize("value", [b"k" * (FIELD_LIMIT + 1), b"\xff\xfe-not-utf8"])
def test_an_overlong_or_non_utf8_value_is_invalid_at_its_field(field, value):
    """Refused at its field in fixed words that never quote it."""
    with pytest.raises(InvalidChange) as raised:
        line_text(value, field)
    [problem] = raised.value.fields
    assert problem["field"] == field and problem["code"] == "invalid"
    assert "not-utf8" not in problem["message"] and "kkk" not in problem["message"]


def test_text_within_the_limit_is_kept_as_given():
    """Normalizing is the page's job (``check_new_key``, ``normalize_code``)."""
    assert line_text(b" abc \n", "code") == " abc \n"


@pytest.mark.parametrize(
    "argv",
    [
        ["integration", "backup-key", "challenge"],
        ["integration", "backup-key", "challenge", "--public-key", "x"],
        ["integration", "backup-key", "challenge", "--public-key-file", "x"],
        ["integration", "backup-key", "preview", "--code-stdin"],
        ["integration", "backup-key", "preview", "--intent", "x"],
        ["integration", "backup-key", "preview", "--intent", "x", "--code", "y"],
        ["integration", "backup-key", "confirm"],
        ["integration", "backup-key", "confirm", "backup_key", "--token", "x"],
        ["integration", "backup-key"],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """No key line option, no intent, a value as an option, no token."""
    out = io.StringIO()
    code = admin_cli.main(
        [*argv, "--config", "web.yaml", "--session-stdin"],
        stdin=io.BytesIO(b""),
        stdout=out,
        stderr=out,
    )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"


@pytest.mark.parametrize(
    ("handler", "args"),
    [
        (admin_cli.integration_backup_key_challenge, Namespace()),
        (admin_cli.integration_backup_key_preview, Namespace(intent="x")),
    ],
)
def test_the_role_is_checked_before_the_line_is_read(handler, args, monkeypatch):
    """Staff is denied before the public key or code line is read (#1339)."""
    calls = []

    def denied(caller, service, *, read_only=False, capability=None):
        """Refuse as the page does for a user who is now Staff."""
        calls.append(read_only)
        raise PermissionError("This page requires the configure capability.")

    def unexpected(*arguments, **options):
        """Nothing after the role check may run."""
        raise AssertionError("read before the role check")

    monkeypatch.setattr(admin_changes, "_admit_change", denied)
    monkeypatch.setattr(admin_cli, "read_secret", unexpected)
    monkeypatch.setattr(admin_backup_key, "challenge", unexpected)
    monkeypatch.setattr(admin_backup_key, "preview", unexpected)
    with pytest.raises(PermissionError):
        handler(args, None, None, {"caller": None})
    assert calls == [True]


def test_an_altered_token_is_refused_before_the_prompt():
    """A token the page did not sign never reaches the prompt."""
    with pytest.raises(ValueError, match="not valid"):
        admin_backup_key.prompt_lines(None, None, "not-a-token")
