"""``integration key status``, Finish switching and key replacement: pure tests.

The catalog entries, the ledger, usage errors and the key line. The commands against a
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
    KeyReplacement,
    KeyStatus,
    ReplaceReview,
    key_text,
    replace_prompt_lines,
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
    """The results carry request ids, states and public settings only
    (``key_request_id`` is the key change's id, not the key)."""
    for model in (KeyStatus, FinishPreview, FinishConfirm, KeyReplacement):
        for name in model.field_names():
            assert "fingerprint" not in name, name
            assert "key" not in name or name == "key_request_id", name


REPLACE_EVENT = "admin_cmd_integration_key_replace"


def test_the_replacement_catalog_entry():
    """Full scope, fresh-gated, prompting, against a version, with its own
    described event; the key comes only as the line after the preamble."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    replace = entries["integration key replace"]
    assert replace["scope"] == "full" and replace["changes_state"]
    assert replace["prompts"] and replace["fresh_gated"]
    assert replace["expected_version"] and not replace["request_key"]
    assert replace["audit_event"] == REPLACE_EVENT
    assert Action(REPLACE_EVENT) and REPLACE_EVENT in DESCRIPTIONS
    assert replace["pr"] == 10
    assert replace["result_fields"] == list(KeyReplacement.field_names())
    assert replace["arguments"] == ["TARGET"]
    options = {option["name"]: option for option in replace["options"]}
    assert options["--secret-stdin"]["required"]
    assert not options["--secret-stdin"]["takes_value"]
    assert options["--expected-version"]["required"]
    assert not options["--changes"]["required"]
    # No option takes the key itself.
    assert not {"--secret", "--key", "--secret-file"} & set(options)


def test_the_ledger_covers_the_status_and_the_switch():
    """Both routes are covered, and key replacement on the settings page;
    the Backup encryption key is still owed by PR 10."""
    entry = LEDGER["integration_settings"]
    assert "integration key replace" in entry.detail and entry.owed == "PR 10"
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
        ["integration", "key", "replace", "slack", "--expected-version", "a" * 64],
        ["integration", "key", "replace", "slack", "--secret-stdin"],
        ["integration", "key", "replace", "backup", "--secret-stdin"],
        [
            "integration",
            "key",
            "replace",
            "slack",
            "--expected-version",
            "a" * 64,
            "--secret-stdin",
            "--changes",
            "-",
            "--yes",
        ],
    ],
)
def test_malformed_commands_are_usage_errors(argv):
    """No or a keyless integration, a malformed request id, no token, a key
    replacement without its version or key line, or with --changes -."""
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


def test_the_role_is_checked_before_any_lookup_or_prompt(monkeypatch):
    """Finish switching checks the Administrator role (renewing nothing)
    before it reads the token, looks up the key change or prompts (#1339)."""
    from argparse import Namespace

    from parishkit.stewardship import admin_changes, admin_integration_keys

    calls = []

    def denied(caller, service, *, read_only=False, capability=None):
        """Refuse as the page does for a user who is now Staff."""
        calls.append(read_only)
        raise PermissionError("This page requires the configure capability.")

    def unexpected(*arguments, **options):
        """Nothing after the role check may run."""
        raise AssertionError("looked up or prompted before the role check")

    monkeypatch.setattr(admin_changes, "_admit_change", denied)
    monkeypatch.setattr(admin_integration_keys, "finish_prompt_lines", unexpected)
    monkeypatch.setattr(admin_cli, "_input", unexpected)
    monkeypatch.setattr(admin_cli, "confirm", unexpected)
    args = Namespace(target="parishsoft", token="-")
    with pytest.raises(PermissionError):
        admin_cli.integration_key_finish_switching(args, None, None, {"caller": None})
    assert calls == [True]


def _line(body):
    """A key line after the preamble, as bytes on standard input."""
    return io.BytesIO(b"pk-admin-secret/1 " + body + b"\n")


def test_the_key_line_is_decoded():
    """The tagged base64 line gives back the exact bytes."""
    import base64

    value = b"  xoxb-synthetic\n"
    assert admin_cli.read_secret(_line(base64.b64encode(value))) == value


@pytest.mark.parametrize(
    "stream",
    [
        b"",
        b"pk-admin-secret/1 c2VjcmV0",
        b"pk-admin-session/1 c2VjcmV0\n",
        b"pk-admin-secret/1 not base64!\n",
        b"pk-admin-secret/1 " + b"A" * 200_000 + b"\n",
    ],
)
def test_a_malformed_key_line_is_a_usage_error_that_names_nothing(stream):
    """Missing, unterminated, mistagged, not base64 or over the limit."""
    with pytest.raises(admin_cli.UsageError) as raised:
        admin_cli.read_secret(io.BytesIO(stream))
    assert "c2VjcmV0" not in str(raised.value)


@pytest.mark.parametrize("value", [b"k" * (128 * 1024 + 1), b"\xff\xfe-not-utf8"])
def test_an_overlong_or_non_utf8_key_is_invalid_at_the_key(value):
    """Refused at ``key`` in fixed words that never quote the key."""
    from parishkit.stewardship.admin_changes import InvalidChange

    with pytest.raises(InvalidChange) as raised:
        key_text(value)
    [problem] = raised.value.fields
    assert problem["field"] == "key" and problem["code"] == "invalid"
    assert "not-utf8" not in problem["message"] and "kkk" not in problem["message"]


def test_the_prompt_names_settings_never_values():
    """Replacing or setting up, the settings saved with it, and that a
    refused key changes nothing."""
    review = ReplaceReview(
        "v",
        "slack",
        "Slack",
        False,
        [{"field": "channel_id", "before": "C0SECRET1", "after": "C0SECRET2"}],
    )
    lines = replace_prompt_lines(review)
    assert lines[0] == "This replaces the Slack key with the key given."
    assert lines[1] == "It also changes the Slack settings: channel_id."
    assert "current key stays in use" in lines[2]
    assert "C0SECRET" not in " ".join(lines)
    setup = replace_prompt_lines(ReplaceReview("v", "slack", "Slack", True, []))
    assert setup[0] == "This sets up Slack with the key given." and len(setup) == 2


def test_the_role_is_checked_before_the_key_is_read(monkeypatch):
    """Staff is denied before the key line is read or anything looked up."""
    from argparse import Namespace

    from parishkit.stewardship import admin_changes

    def denied(caller, service, *, read_only=False, capability=None):
        """Refuse as the page does for a user who is now Staff."""
        raise PermissionError("This page requires the configure capability.")

    def unexpected(*arguments, **options):
        """Nothing after the role check may run."""
        raise AssertionError("read the key before the role check")

    monkeypatch.setattr(admin_changes, "_admit_change", denied)
    monkeypatch.setattr(admin_cli, "read_secret", unexpected)
    args = Namespace(target="slack", expected_version="a" * 64, changes=None)
    with pytest.raises(PermissionError):
        admin_cli.integration_key_replace(args, None, None, {"caller": None})


class _Errors:
    """A form's ``errors`` stand-in."""

    def __init__(self, data):
        self.data = data

    def get_json_data(self):
        """Django's ``ErrorDict.get_json_data`` shape."""
        return self.data


class _Form:
    """An ``InlineCredentialForm`` stand-in: valid or not, with errors."""

    def __init__(self, *, valid, candidate="", errors=None):
        self.valid = valid
        self.cleaned_data = {"candidate": candidate} if valid else {}
        self.errors = _Errors(errors or {})

    def is_valid(self):
        """Whether the form is valid."""
        return self.valid


def test_every_key_form_problem_is_a_refusal_at_the_key():
    """A valid key is no problem; an empty one is ``required``; the
    candidate's errors are reported; an error at the intent alone (which the
    command signs itself) is still a fixed refusal, not a KeyError."""
    from parishkit.stewardship.admin_integration_keys import _credential_problems

    assert _credential_problems(_Form(valid=True, candidate="k")) == []
    [empty] = _credential_problems(_Form(valid=True))
    assert empty["field"] == "key" and empty["code"] == "required"
    candidate = {"candidate": [{"code": "max_length", "message": "Too long."}]}
    [long] = _credential_problems(_Form(valid=False, errors=candidate))
    assert long["field"] == "key" and long["message"] == "Too long."
    intent = {"intent": [{"code": "invalid", "message": "Bad intent."}]}
    [other] = _credential_problems(_Form(valid=False, errors=intent))
    assert other["field"] == "key" and other["code"] == "invalid"
    assert other["message"] == "The key could not be checked."
