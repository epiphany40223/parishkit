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
from uuid import uuid4

import pytest
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, IntegrityError, OperationalError

from parishkit.config import ConfigError
from parishkit.stewardship import admin_cli, cli
from parishkit.stewardship.accounts.authority import AuthorityChanging
from parishkit.stewardship.accounts.automation_sessions import (
    SessionUnusable,
    command_event_type,
)
from parishkit.stewardship.admin_reads import NotAvailable, Unavailable
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
    session = {"login start", "login wait", "logout", "whoami", "sessions", "commands"}
    reads = {
        "status",
        "task list",
        "task show",
        "send progress",
        "send history",
        "schedule show",
        "go-live readiness",
        "go-live progress",
        "system health",
    }
    changes = {"schedule preview", "schedule confirm", "config request show"}
    assert set(entries) == session | reads | changes
    for entry in entries.values():
        names = {option["name"] for option in entry["options"]}
        assert {"--config", "--session-stdin"} <= names
        assert entry["pr"] == (
            2 if entry["name"] in session else 4 if entry["name"] in changes else 3
        )
        assert not entry["fresh_gated"] and not entry["prompts"]
    for name in reads:
        # Read-only status: any session, no state change, the page's event.
        assert entries[name]["scope"] == "read_only", name
        assert not entries[name]["changes_state"], name
        assert entries[name]["watch"] == (
            name in {"task show", "send progress", "go-live progress", "system health"}
        )
    assert entries["status"]["audit_event"] == "dashboard_viewed"
    assert entries["task list"]["audit_event"] == "background_viewed"
    assert entries["send history"]["audit_event"] == "delivery_viewed"
    # The pages record no view event, so neither do these reads.
    for name in ("schedule show", "go-live readiness", "go-live progress"):
        assert entries[name]["audit_event"] is None, name
        campaign = {option["name"] for option in entries[name]["options"]}
        assert "--campaign" in campaign, name
    assert entries["task show"]["arguments"] == ["TASK_ID"]
    assert entries["system health"]["audit_event"] == "system_health_viewed"
    for name in ("send progress", "go-live progress", "system health"):
        watch = {option["name"] for option in entries[name]["options"]}
        assert {"--watch", "--timeout"} <= watch, name
    states = {option["name"]: option for option in entries["task list"]["options"]}
    assert states["--state"]["choices"][:2] == ["nonterminal", "all"]
    start = {option["name"]: option for option in entries["login start"]["options"]}
    assert start["--scope"]["choices"] == ["read-only", "full"]
    assert start["--label"]["required"] and not start["--days"]["required"]
    assert entries["login start"]["scope"] == "none"
    # The catalog needs no session at all.
    assert entries["commands"]["scope"] == "none"
    assert entries["logout"]["audit_event"] == "automation_session_ended"
    assert entries["whoami"]["result_fields"][:2] == ["id", "label"]
    # The schedule change commands need a full-scope session; only the
    # confirmation changes state and records its admin_cmd_* event.
    preview, confirm = entries["schedule preview"], entries["schedule confirm"]
    assert preview["scope"] == confirm["scope"] == "full"
    assert not preview["changes_state"] and preview["audit_event"] is None
    assert preview["expected_version"] and not preview["request_key"]
    assert confirm["changes_state"] and not confirm["expected_version"]
    assert confirm["audit_event"] == "admin_cmd_schedule_confirm"
    assert preview["result_fields"][-1] == "preview"
    for entry in (preview, confirm):
        options = {option["name"]: option for option in entry["options"]}
        assert "--campaign" in options and not options["--campaign"]["required"]
    request = entries["config request show"]
    assert request["scope"] == "read_only" and not request["changes_state"]
    assert request["arguments"] == ["REQUEST_ID"] and request["watch"]
    assert request["audit_event"] is None


def test_every_state_change_has_a_registered_described_event():
    """Each admin_cmd_* type is an audit Action with a log description."""
    from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS
    from parishkit.stewardship.audit.schemas import Action

    events = [
        entry["audit_event"]
        for entry in admin_cli.catalog()
        if (entry["audit_event"] or "").startswith("admin_cmd_")
    ]
    assert events == ["admin_cmd_schedule_confirm"]
    for event in events:
        assert Action(event) and event in DESCRIPTIONS, event


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
        ["task", "show", "not-a-uuid", "--config", "x", "--session-stdin"],
        # Canonical UUIDs only: no upper case, braces or missing hyphens.
        [
            "task",
            "show",
            "00000000-0000-4000-8000-00000000000A",
            "--config",
            "x",
            "--session-stdin",
        ],
        ["send", "progress", "--watch", "1", "--config", "x", "--session-stdin"],
        ["send", "progress", "--watch", "301", "--config", "x", "--session-stdin"],
        [
            "send",
            "progress",
            "--watch",
            "2",
            "--timeout",
            "10801",
            "--config",
            "x",
            "--session-stdin",
        ],
        ["status", "--watch", "2", "--config", "x", "--session-stdin"],
        # --timeout belongs to --watch; alone it is refused, not ignored.
        ["send", "progress", "--timeout", "60", "--config", "x", "--session-stdin"],
        ["task", "list", "--state", "done", "--config", "x", "--session-stdin"],
        # Readiness is one read, never watched; progress takes the 3a limits.
        ["go-live", "readiness", "--watch", "2", "--config", "x", "--session-stdin"],
        ["go-live", "progress", "--watch", "1", "--config", "x", "--session-stdin"],
        ["go-live", "progress", "--timeout", "60", "--config", "x", "--session-stdin"],
        [
            "go-live",
            "readiness",
            "--campaign",
            "not-a-uuid",
            "--config",
            "x",
            "--session-stdin",
        ],
        # PR 4: the change needs its version (a configuration digest) and
        # document, the confirmation its token; a group alone is no command.
        ["schedule", "preview", "--changes", "{}", "--config", "x", "--session-stdin"],
        [
            "schedule",
            "preview",
            "--expected-version",
            "601",
            "--changes",
            "{}",
            "--config",
            "x",
            "--session-stdin",
        ],
        [
            "schedule",
            "preview",
            "--expected-version",
            "a" * 64,
            "--config",
            "x",
            "--session-stdin",
        ],
        ["schedule", "confirm", "--config", "x", "--session-stdin"],
        ["config", "--config", "x", "--session-stdin"],
        ["config", "request", "--config", "x", "--session-stdin"],
        ["config", "request", "show", "601", "--config", "x", "--session-stdin"],
        ["config", "request", "show", "--config", "x", "--session-stdin"],
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
        # Read refusals (PR 3): a restore under review or an activating
        # configuration is temporary; an unknown task or campaign is not
        # available; a watch past its limit times out.
        (Unavailable("restore"), True, False, "unavailable"),
        (AuthorityChanging("activating"), True, False, "unavailable"),
        (AuthorityChanging("activating"), False, False, "unavailable"),
        (NotAvailable("no task"), True, False, "not_available"),
        (ObjectDoesNotExist("gone"), True, False, "not_available"),
        # From a command that may have changed something, a missing row is
        # no proof that nothing changed.
        (ObjectDoesNotExist("gone"), True, True, "outcome_unknown"),
        # A bare LookupError or KeyError is a bug, never "not available".
        (LookupError("bug"), True, False, "internal"),
        (KeyError("bug"), True, False, "internal"),
        (admin_cli.WatchTimeout(None), True, False, "watch_timeout"),
        (admin_cli.WatchInterrupted(None), True, False, "watch_interrupted"),
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


def test_the_runtime_is_configured_only_after_setup():
    """``configured()`` reads the setup marker each time and fails closed."""
    runtime = admin_cli.AdminRuntime(store=None, pairing=None, public_origin="x")
    assert runtime.configured() is False
    runtime.setup_complete = lambda: True
    assert runtime.configured() is True
    runtime.setup_complete = lambda: "yes"
    with pytest.raises(ConfigError):
        runtime.configured()


def test_canonical_uuids_are_accepted():
    """The lower-case hyphenated form is the only accepted spelling."""
    value = "00000000-0000-4000-8000-00000000000a"
    assert str(admin_cli._uuid(value)) == value


def test_the_task_state_filter_is_the_pages():
    """Spelled out for the parser, it stays equal to the task states."""
    from parishkit.stewardship.jobs.models import TASK_STATES

    assert ("nonterminal", "all", *TASK_STATES) == admin_cli.TASK_STATE_FILTERS


# Unexpected failures leave a trace (#612). The text every forced error
# carries, which no log line may show.
PRIVATE = "private-value /private/path"


def run_failing(monkeypatch, handler, *, changes_state=False, load=None):
    """Run a stand-in command whose handler fails; returns (exit, document).

    Admission is replaced by an empty context, so the handler runs as an
    admitted, session-less command; ``load`` replaces ``load_deployment`` to
    fail before admission instead.
    """
    import contextlib
    from types import SimpleNamespace

    spec = admin_cli.CommandSpec(
        name="probe",
        help="A stand-in command.",
        handler=handler,
        scope="none",
        changes_state=changes_state,
        result_fields=(),
        pr=0,
    )
    monkeypatch.setitem(admin_cli.BY_NAME, "probe", spec)
    # Debug logging adds detail (and the traceback) the tests compare against.
    monkeypatch.delenv("PARISHKIT_DEBUG_LOGGING", raising=False)
    monkeypatch.setattr(
        "parishkit.stewardship.deployment.load_deployment",
        load or (lambda path: object()),
    )
    monkeypatch.setattr(
        admin_cli, "ADMISSION", lambda configuration: contextlib.nullcontext(None)
    )
    out = io.StringIO()
    code = admin_cli.run(
        SimpleNamespace(command_name="probe", config=Path("x")),
        stdin=io.BytesIO(PREAMBLE),
        stdout=out,
        stderr=io.StringIO(),
    )
    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    return code, json.loads(lines[0])


def failure_lines(caplog):
    """The formatted structured lines logged, as the deployed process writes them."""
    from parishkit.stewardship.observability import Event, SafeJsonFormatter

    formatter = SafeJsonFormatter()
    return [
        formatter.format(record)
        for record in caplog.records
        if isinstance(record.msg, Event)
    ]


def broken(args, preamble, runtime, context):
    """A read handler that fails with an error nothing classifies."""
    raise RuntimeError(PRIVATE)


def test_an_unexpected_read_error_logs_one_failure_line(monkeypatch, caplog):
    """``internal`` after admission: one task_failed line joined by correlation id.

    The line names the category and the exception's class, never its text,
    and the document is the one the specification shows.
    """
    import logging

    with caplog.at_level(logging.DEBUG):
        code, document = run_failing(monkeypatch, broken)
    assert code == 3
    assert document == {
        "schema": "pk-admin/1",
        "command": "probe",
        "correlation_id": document["correlation_id"],
        "ok": False,
        "final": True,
        "session": None,
        "error": {"code": "internal", "message": admin_cli.MESSAGES["internal"]},
    }
    lines = failure_lines(caplog)
    assert len(lines) == 1 and PRIVATE not in lines[0]
    line = json.loads(lines[0])
    assert line["message"] == "task_failed" and line["level"] == "ERROR"
    assert line["extra"] == {
        "correlation_id": document["correlation_id"],
        "failure_kind": "unexpected_failure",
        "error_class": "builtins.RuntimeError",
    }


def test_an_unexpected_error_before_admission_is_a_startup_rejection(
    monkeypatch, caplog
):
    """#609's shape: an AttributeError while loading the configuration."""
    import logging

    def load(path):
        """Fail as #609's string --config did."""
        raise AttributeError(PRIVATE)

    with caplog.at_level(logging.DEBUG):
        code, document = run_failing(monkeypatch, broken, load=load)
    assert code == 3 and document["error"]["code"] == "internal"
    lines = failure_lines(caplog)
    assert len(lines) == 1 and PRIVATE not in lines[0]
    line = json.loads(lines[0])
    assert line["message"] == "startup_rejected"
    assert line["extra"] == {
        "correlation_id": document["correlation_id"],
        "failure_kind": "unexpected_failure",
        "error_class": "builtins.AttributeError",
    }


def test_an_unknown_outcome_logs_one_failure_line(monkeypatch, caplog):
    """``outcome_unknown`` after a durable commit names the error's category."""
    import logging

    def committed(args, preamble, runtime, context):
        """Commit, then lose the database."""
        context["committed"] = True
        raise OperationalError(PRIVATE)

    with caplog.at_level(logging.DEBUG):
        code, document = run_failing(monkeypatch, committed, changes_state=True)
    assert code == 6 and document["error"]["code"] == "outcome_unknown"
    lines = failure_lines(caplog)
    assert len(lines) == 1 and PRIVATE not in lines[0]
    assert json.loads(lines[0])["extra"] == {
        "correlation_id": document["correlation_id"],
        "failure_kind": "database_unavailable",
        "error_class": "django.db.utils.OperationalError",
    }


@pytest.mark.parametrize(
    "error,expected",
    [
        (ValueError(PRIVATE), "invalid"),
        (PermissionError(PRIVATE), "denied"),
        (OperationalError(PRIVATE), "unavailable"),
        # The positive control: the same setup traces an unexpected error.
        (RuntimeError(PRIVATE), "internal"),
    ],
    ids=["invalid", "denied", "unavailable", "internal"],
)
def test_only_an_unexpected_error_logs_a_failure_line(
    monkeypatch, caplog, error, expected
):
    """A refusal or an outage has its own code; only the unexpected is traced."""
    import logging

    def failing(args, preamble, runtime, context):
        """Fail with the given error."""
        raise error

    with caplog.at_level(logging.DEBUG):
        code, document = run_failing(monkeypatch, failing)
    assert document["error"]["code"] == expected
    assert len(failure_lines(caplog)) == (expected in admin_cli.TRACED_CODES)


def test_a_failure_to_log_keeps_the_document(monkeypatch):
    """Logging is best effort: the document and exit code stay the contract."""

    def fail(*args, **kwargs):
        """A logging failure."""
        raise RuntimeError("logging failed")

    monkeypatch.setattr("parishkit.stewardship.observability.emit_failure", fail)
    code, document = run_failing(monkeypatch, broken)
    assert code == 3 and document["error"]["code"] == "internal"


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
if sys.argv[1] == "broken":
    from parishkit.stewardship import deployment
    def load_deployment(path):
        raise AttributeError("private-value /private/path")
    deployment.load_deployment = load_deployment
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
        # An existing file goes through the real load_deployment, past its
        # Path handling (#609: a string --config ended as "internal"), to a
        # later admission check, which refuses this development file.
        ("file", PREAMBLE, ["whoami"], (2, "configuration")),
        (
            "file",
            PREAMBLE,
            [
                "login",
                "start",
                "--name",
                "ops",
                "--label",
                "x",
                "--expect-email",
                "a@example.org",
                "--scope",
                "read-only",
            ],
            (2, "configuration"),
        ),
        # Past load_deployment: every session command reaches the signing
        # keyring check inside configure_admin_process, never "internal".
        ("stub", PREAMBLE, ["whoami"], (2, "configuration")),
        ("stub", PREAMBLE, ["sessions"], (2, "configuration")),
        ("stub", PREAMBLE, ["logout"], (2, "configuration")),
        ("stub", PREAMBLE, ["login", "wait", "--name", "ops"], (2, "configuration")),
        # A read with --watch parses and reaches admission the same way.
        (
            "stub",
            PREAMBLE,
            ["go-live", "progress", "--watch", "2"],
            (2, "configuration"),
        ),
        # The schedule change commands and the three-word request status.
        (
            "stub",
            PREAMBLE,
            ["schedule", "preview", "--expected-version", "a" * 64, "--changes", "{}"],
            (2, "configuration"),
        ),
        (
            "stub",
            PREAMBLE,
            ["schedule", "confirm", "--token", "t"],
            (2, "configuration"),
        ),
        (
            "stub",
            PREAMBLE,
            ["config", "request", "show", str(uuid4()), "--watch", "2"],
            (2, "configuration"),
        ),
    ],
    ids=[
        "commands",
        "malformed-preamble",
        "missing-configuration",
        "real-configuration-whoami",
        "real-configuration-login-start",
        "admitted-whoami",
        "admitted-sessions",
        "admitted-logout",
        "admitted-login-wait",
        "admitted-go-live-progress",
        "admitted-schedule-preview",
        "admitted-schedule-confirm",
        "admitted-config-request-show",
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

    config = tmp_path / "missing.yaml"
    if mode == "file":
        # A real, existing deployment file, read by the real load_deployment.
        config = tmp_path / "web.yaml"
        config.write_text("deployment:\n  profile: development\n")
        mode = "real"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            FRESH_PROCESS,
            mode,
            *argv,
            "--config",
            str(config),
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


def test_a_fresh_process_logs_an_unexpected_error_on_standard_error():
    """#612 end to end: the real logging setup, before Django is set up.

    Standard error carries one startup_rejected line that names the
    error's class and shares the document's correlation id, and no line
    carries the exception's text; standard output is the document alone.
    """
    import subprocess
    import sys

    # Debug logging would add the traceback, and with it the text.
    environment = fresh_environment()
    environment.pop("PARISHKIT_DEBUG_LOGGING", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            FRESH_PROCESS,
            "broken",
            "whoami",
            "--config",
            "x",
            "--session-stdin",
        ],
        input=PREAMBLE,
        capture_output=True,
        env=environment,
        timeout=120,
    )
    stderr = result.stderr.decode()
    assert result.returncode == 0, stderr[-2000:]
    document, outcome = map(json.loads, result.stdout.decode().splitlines())
    assert outcome["exit"] == 3 and document["error"]["code"] == "internal"
    assert "private" not in stderr
    # Deliberately strict: every standard error line must be structured
    # JSON, so stray unstructured output fails here too.
    lines = [json.loads(line) for line in stderr.splitlines()]
    failures = [line for line in lines if line["message"] == "startup_rejected"]
    assert len(failures) == 1
    assert failures[0]["extra"] == {
        "correlation_id": document["correlation_id"],
        "failure_kind": "unexpected_failure",
        "error_class": "builtins.AttributeError",
    }


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
    for name in (
        "main",
        "run",
        "read_preamble",
        "catalog",
        "command_event_type",
        # The command specifications are built when the module is imported.
        "_read_specs",
        "_change_specs",
    ):
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
