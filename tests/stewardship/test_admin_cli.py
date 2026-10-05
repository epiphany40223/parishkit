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


# Runs the command line in a fresh process with no Django settings, as the
# wrapper does in the container. The preamble arrives on standard input,
# never on the command line. With "stub" as the first argument, the
# deployment and host checks are replaced, so the real admitted() and
# configure_admin_process() run up to the missing signing keyring: every
# import they make before Django is set up really happens.
FRESH_PROCESS = """
import contextlib, io, json, sys
from types import SimpleNamespace
if sys.argv[1] == "stub":
    from parishkit.stewardship import (
        deployment, runtime_paths, runtime_web, service_boundaries,
        startup_interlock,
    )
    deployment.load_deployment = lambda path: SimpleNamespace(secrets={})
    service_boundaries.admit_online_service = (
        lambda configuration: deployment.ServiceRole.WEB
    )
    runtime_web.admit_lifecycle_mounts = lambda configuration: None
    runtime_paths.RuntimeLayout = lambda configuration: SimpleNamespace(
        interlock=None
    )
    startup_interlock.StartupLease = lambda *a, **k: contextlib.nullcontext()
from parishkit.stewardship import cli
sys.stdin = io.TextIOWrapper(io.BytesIO(sys.stdin.buffer.read()))
code = cli.main(["admin", *sys.argv[2:]])
print(json.dumps({"exit": code}))
"""


def fresh_environment():
    """This interpreter's environment without Django settings, with this source."""
    import os

    environment = {
        key: value
        for key, value in os.environ.items()
        if key != "DJANGO_SETTINGS_MODULE"
    }
    environment["PYTHONPATH"] = str(SOURCE.parent.parent)
    return environment


@pytest.mark.parametrize(
    "mode,preamble,argv,expected",
    [
        ("real", PREAMBLE, ["commands"], (0, None)),
        ("real", b"malformed\n", ["commands"], (2, "usage")),
        # Admission fails on the missing configuration file: exit 2, mapped
        # without loading any model.
        ("real", PREAMBLE, ["whoami"], (2, "configuration")),
        # Past load_deployment: every session command reaches the signing
        # keyring check inside configure_admin_process, never "internal".
        ("stub", PREAMBLE, ["whoami"], (2, "configuration")),
        ("stub", PREAMBLE, ["sessions"], (2, "configuration")),
        ("stub", PREAMBLE, ["logout"], (2, "configuration")),
        ("stub", PREAMBLE, ["login", "wait", "--name", "ops"], (2, "configuration")),
    ],
    ids=[
        "commands",
        "malformed-preamble",
        "missing-configuration",
        "admitted-whoami",
        "admitted-sessions",
        "admitted-logout",
        "admitted-login-wait",
    ],
)
def test_the_command_line_runs_before_django_is_set_up(
    tmp_path, mode, preamble, argv, expected
):
    """The wrapper runs each command in a fresh process with no Django settings.

    The preamble, the catalog, the exit-code mapping and admission up to
    configure_admin_process all run before Django is set up, so none of
    them may import a model.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            FRESH_PROCESS,
            mode,
            *argv,
            "--config",
            str(tmp_path / "missing.yaml"),
            "--session-stdin",
        ],
        input=preamble,
        capture_output=True,
        env=fresh_environment(),
        timeout=120,
    )
    lines = result.stdout.decode().splitlines()
    assert result.returncode == 0, result.stderr.decode()[-2000:]
    document, outcome = json.loads(lines[0]), json.loads(lines[-1])
    code, error = expected
    assert (document.get("error") or {}).get("code") == error
    assert outcome["exit"] == code


def pre_setup_imports():
    """Every module admin_cli imports before Django is set up, read from its source.

    Module-level imports; in ``admitted()``, the imports before its call to
    ``configure_admin_process``; in ``configure_admin_process``, the imports
    before ``settings.configure`` (and the settings module it loads); and the
    direct imports of the functions that run before admission.
    """
    import ast

    tree = ast.parse(Path(admin_cli.__file__).read_text())
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }

    def calls(node, name):
        """The first line on which ``node`` calls ``name`` (an attribute or a name)."""
        lines = [
            item.lineno
            for item in ast.walk(node)
            if isinstance(item, ast.Call)
            and getattr(item.func, "attr", getattr(item.func, "id", None)) == name
        ]
        return min(lines)

    def imports(statements, before=None):
        """Absolute module names imported by these statements, before a line."""
        names = set()
        for statement in statements:
            for item in ast.walk(statement):
                if before is not None and getattr(item, "lineno", 0) >= before:
                    continue
                if isinstance(item, ast.Import):
                    names.update(alias.name for alias in item.names)
                elif isinstance(item, ast.ImportFrom):
                    package = "parishkit.stewardship"
                    if item.level == 2:
                        package = "parishkit"
                    module = item.module or ""
                    names.add(
                        f"{package}.{module}".rstrip(".") if item.level else module
                    )
        return names

    found = imports(
        [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    )
    admitted = functions["admitted"]
    found |= imports(admitted.body, calls(admitted, "configure_admin_process"))
    configure = functions["configure_admin_process"]
    found |= imports(configure.body, calls(configure, "configure"))
    found.add("parishkit.stewardship.settings.base")
    for name in ("main", "run", "read_preamble", "catalog", "command_event_type"):
        # Only the function's own direct imports: nested blocks run later.
        found |= imports(
            [
                node
                for node in functions[name].body
                if isinstance(node, (ast.Import, ast.ImportFrom))
            ]
        )
    return sorted(found)


def test_every_pre_setup_import_is_django_free():
    """Each module imported before Django is set up loads in a clean process.

    A static guard beside the fresh-process test: a model-loading import
    added before configure_admin_process fails here by name, whichever
    command path would reach it.
    """
    import subprocess
    import sys

    modules = pre_setup_imports()
    assert "parishkit.stewardship.accounts.authority" in modules
    assert "parishkit.stewardship.accounts.automation_sessions" not in modules
    failed = []
    for module in modules:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True,
            env=fresh_environment(),
            timeout=120,
        )
        if result.returncode:
            failed.append(module)
    assert not failed, f"These load Django models before setup: {failed}"
