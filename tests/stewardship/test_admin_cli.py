"""``pk-stewardship admin``'s command shape, documents and refusals (ADM-11).

Pure tests: the subparser tree and its catalog, the preamble, usage errors
as documents, the exception-to-exit mapping in the specification's order and
the audit event type each command would record. The commands against a real
database are in database/test_admin_cli_postgresql.py.
"""

import io
import json
import re
from pathlib import Path

import pytest
from django.db import DatabaseError, IntegrityError, OperationalError

from parishkit.config import ConfigError
from parishkit.stewardship import admin_cli, cli
from parishkit.stewardship.accounts.automation_sessions import (
    SessionUnusable,
    command_event_type,
)
from parishkit.stewardship.startup_interlock import StartupBusy
from parishkit.stewardship.storage import StaleRecordError

SOURCE = Path(admin_cli.__file__).parent
SECRET = "A" * 43
HOST = "f" * 64
PREAMBLE = f"pk-admin-session/1 {SECRET} {HOST}\n".encode()


def run(argv, stdin=PREAMBLE):
    """Run the admin command line; returns (exit code, document)."""
    out = io.StringIO()
    code = admin_cli.main(argv, stdin=io.BytesIO(stdin), stdout=out, stderr=out)
    return code, json.loads(out.getvalue().splitlines()[-1])


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """The real logging setup leaves a global stderr handler behind."""
    monkeypatch.setattr(
        "parishkit.stewardship.observability.configure_logging", lambda: None
    )


def test_the_catalog_lists_every_command_with_its_flags():
    """Generated from the subparser tree: names, scopes, options and fields."""
    entries = {entry["name"]: entry for entry in admin_cli.catalog()}
    assert set(entries) == {spec.name for spec in admin_cli.COMMANDS}
    assert set(entries) == {
        "login start",
        "login wait",
        "logout",
        "whoami",
        "sessions",
        "commands",
    }
    for entry in entries.values():
        names = {option["name"] for option in entry["options"]}
        assert {"--config", "--session-stdin"} <= names
        assert entry["pr"] == 2
        assert not entry["fresh_gated"] and not entry["prompts"]
    start = {option["name"]: option for option in entries["login start"]["options"]}
    assert start["--scope"]["choices"] == ["read-only", "full"]
    assert start["--label"]["required"] and not start["--days"]["required"]
    assert entries["login start"]["scope"] == "none"
    # The catalog needs no session at all.
    assert entries["commands"]["scope"] == "none"
    assert entries["logout"]["audit_event"] == "automation_session_ended"
    assert entries["whoami"]["result_fields"][:2] == ["id", "label"]


def test_every_command_maps_to_a_valid_audit_event_type():
    """admin_cmd_<area>_<verb> fits the event-type pattern within 64 characters."""
    pattern = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
    for spec in admin_cli.COMMANDS:
        assert pattern.fullmatch(command_event_type(spec.name)), spec.name
    assert (
        command_event_type("go-live confirm-preview")
        == "admin_cmd_go_live_confirm_preview"
    )


def test_commands_needs_no_database_and_prints_one_document():
    """The catalog is printed from the parser; nothing is admitted."""
    code, document = run(["commands", "--config", "x", "--session-stdin"])
    assert code == 0 and document["ok"] and document["schema"] == "pk-admin/1"
    assert document["command"] == "commands" and document["session"] is None
    assert len(document["result"]["commands"]) == len(admin_cli.COMMANDS)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["whoami"],
        ["whoami", "--config", "x"],
        ["whoami", "--session-stdin"],
        ["login", "start", "--config", "x", "--session-stdin"],
        ["login", "wait", "--config", "x", "--session-stdin", "--timeout", "601"],
        ["unknown", "--config", "x", "--session-stdin"],
    ],
)
def test_usage_errors_are_documents_that_name_no_value(argv):
    """Exit 2 with the usage code; argparse's text (may echo input) never shows."""
    out = io.StringIO()
    code = admin_cli.main(argv, stdin=io.BytesIO(PREAMBLE), stdout=out, stderr=out)
    document = json.loads(out.getvalue())
    assert code == 2 and document["error"]["code"] == "usage"
    assert "601" not in out.getvalue()


@pytest.mark.parametrize(
    "preamble",
    [
        b"",
        b"pk-admin-session/1 short " + HOST.encode() + b"\n",
        b"pk-admin-session/2 " + SECRET.encode() + b" " + HOST.encode() + b"\n",
        f"pk-admin-session/1 {SECRET} {HOST}".encode(),
        f"pk-admin-session/1 {SECRET} {HOST} extra\n".encode(),
        b"x" * 300 + b"\n",
    ],
)
def test_a_malformed_preamble_is_a_usage_error(preamble):
    """The first line must be exactly the tagged secret and host digest."""
    code, document = run(["commands", "--config", "x", "--session-stdin"], preamble)
    assert code == 2 and document["error"]["code"] == "usage"
    assert SECRET not in json.dumps(document)


def test_the_preamble_is_read_once_and_nothing_after_it():
    """Later lines stay on the stream for prompts and inputs."""
    stream = io.BytesIO(PREAMBLE + b"next line\n")
    preamble = admin_cli.read_preamble(stream)
    assert preamble.host_digest == HOST and SECRET not in repr(preamble)
    assert stream.read() == b"next line\n"


@pytest.mark.parametrize(
    "error,admitted,changed,expected",
    [
        (StartupBusy("held"), False, False, "busy"),
        (OperationalError("down"), True, False, "unavailable"),
        (DatabaseError("lost"), True, False, "unavailable"),
        (admin_cli.UsageError("bad"), False, False, "usage"),
        (admin_cli.CredentialMismatch("rotating"), False, False, "credential_mismatch"),
        (ConfigError("not the web"), False, False, "configuration"),
        (
            admin_cli.PairingNotFinished("pairing_pending"),
            True,
            False,
            "pairing_pending",
        ),
        (SessionUnusable("session_ended"), True, False, "session_ended"),
        (SessionUnusable("session_missing"), True, False, "session_missing"),
        (StaleRecordError("moved"), True, True, "stale_version"),
        (PermissionError("no"), True, True, "denied"),
        (ValueError("bad"), True, False, "invalid"),
        (ConfigError("domain"), True, False, "invalid"),
        (RuntimeError("boom"), True, True, "outcome_unknown"),
        (RuntimeError("boom"), True, False, "internal"),
    ],
)
def test_exceptions_map_to_codes_in_the_specified_order(
    error, admitted, changed, expected
):
    """StartupBusy before ConfigError; a refusal before an unknown outcome."""
    assert (
        admin_cli.classify(error, admitted_process=admitted, changed=changed)
        == expected
    )


def test_any_error_after_a_durable_commit_is_an_unknown_outcome():
    """An outage after logout committed is exit 6, never exit 3."""
    for error in (OperationalError("down"), DatabaseError("lost"), RuntimeError("x")):
        assert (
            admin_cli.classify(
                error, admitted_process=True, changed=True, committed=True
            )
            == "outcome_unknown"
        )
    # A session refusal raised after an ending commit keeps its own code.
    assert (
        admin_cli.classify(
            SessionUnusable("session_ended"),
            admitted_process=True,
            changed=True,
            committed=True,
        )
        == "session_ended"
    )


def test_a_guard_refusal_is_a_denial_not_an_outage():
    """A constraint or guard answer is exit 1; nothing changed."""

    class Cause(Exception):
        """The driver error a guard's RAISE produces."""

        sqlstate = "23514"

    error = IntegrityError("guard")
    error.__cause__ = Cause()
    assert admin_cli.classify(error, admitted_process=True, changed=True) == "denied"


def test_every_code_has_a_message_and_an_exit():
    """The closed vocabulary of the specification's exit table."""
    assert set(admin_cli.MESSAGES) == set(admin_cli.EXIT_CODES)
    assert {admin_cli.EXIT_CODES[code] for code in admin_cli.EXIT_CODES} == set(
        range(1, 8)
    )


def test_cli_hands_admin_to_its_own_parser(monkeypatch):
    """``pk-stewardship admin …`` is dispatched before the flat parser."""
    seen = []
    monkeypatch.setattr(admin_cli, "main", lambda argv: seen.append(argv) or 7)
    assert cli.main(["admin", "whoami", "--config", "x"]) == 7
    assert seen == [["whoami", "--config", "x"]]


def test_admission_refuses_every_profile_but_the_web(monkeypatch):
    """A worker, installer or offline profile never reaches Django or the database."""
    from parishkit.stewardship.deployment import ServiceRole

    monkeypatch.setattr(
        "parishkit.stewardship.service_boundaries.admit_online_service",
        lambda configuration: ServiceRole.WORKER,
    )
    with pytest.raises(ConfigError), admin_cli.admitted(object()):
        pass


def test_admission_refuses_a_signing_keyring_the_web_did_not_load(monkeypatch):
    """A key rotation in progress (receipts differ) refuses with exit 2."""
    import contextlib
    from types import SimpleNamespace

    from parishkit.stewardship.deployment import ServiceRole

    closed = []
    monkeypatch.setattr(
        "parishkit.stewardship.service_boundaries.admit_online_service",
        lambda configuration: ServiceRole.WEB,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_web.admit_lifecycle_mounts",
        lambda configuration: None,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.startup_interlock.StartupLease",
        lambda *args, **kwargs: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_paths.RuntimeLayout",
        lambda configuration: SimpleNamespace(interlock=None),
    )
    monkeypatch.setattr(admin_cli, "configure_admin_process", lambda c: "a" * 64)
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_grants.admit_runtime_database",
        lambda configuration: None,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.consumer_runtime.loaded_service_receipts",
        lambda configuration: {"django_signing": "b" * 64},
    )
    monkeypatch.setattr("django.db.connections.close_all", lambda: closed.append(True))
    with pytest.raises(admin_cli.CredentialMismatch), admin_cli.admitted(object()):
        pass
    assert closed == [True]
    assert (
        admin_cli.classify(
            admin_cli.CredentialMismatch("x"), admitted_process=False, changed=False
        )
        == "credential_mismatch"
    )
