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
from types import SimpleNamespace
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
    refresh = {"refresh start", "refresh status"}
    tests = {
        "test sample-preview",
        "test sample",
        "test families-preview",
        "test families",
        "test status",
    }
    operations = {
        "task retry",
        "delivery list",
        "delivery show",
        "delivery resolve",
        "delivery resend",
        "delivery refusals",
        "delivery refusal-show",
        "delivery refusal-clear",
    }
    # The commands that ask for the page's acknowledgement (PR 9c). Other
    # branches add their own prompting commands, so this checks these two
    # and that --yes comes exactly with ``prompts``, not a closed list.
    acknowledged = {"delivery resend", "delivery refusal-clear"}
    reports = {
        "logs list",
        "logs export",
        "export create",
        "export status",
        "export cancel",
        "export retry",
        "export regenerate",
        "export download",
        "report list",
        "report participation",
        "report responses",
        "report financial",
        "report talents",
        "report information",
        "report ministry",
        "digest daily",
        "digest weekly",
        "digest weekly-request",
        "export financial",
        "export information",
        "export ministry",
        "export ministry-packet",
        "export directory",
        "export postal",
    }
    assert set(entries) == (
        session | reads | changes | refresh | tests | operations | reports
    )
    for entry in entries.values():
        names = {option["name"] for option in entry["options"]}
        assert {"--config", "--session-stdin"} <= names
        name = entry["name"]
        assert entry["pr"] == (
            2
            if name in session
            else 4
            if name in changes
            else 6
            if name in refresh | tests
            else 9
            if name in operations
            else 8
            if name in reports
            else 3
        )
        # Only the chosen-Family send, the code and financial exports and
        # some regenerations need a recent sign-in on their pages.
        assert entry["fresh_gated"] == (
            name
            in {
                "test families",
                "export regenerate",
                "export financial",
                "export directory",
                "export postal",
            }
        ), name
        # A prompting command, and only one, takes --yes. Other branches add
        # their own prompting commands, so this is not a closed list.
        assert ("--yes" in names) == entry["prompts"], name
        # Only the two downloads stream a file to standard output.
        assert entry["streams"] == (name in {"logs export", "export download"}), name
        if name in acknowledged:
            assert entry["prompts"], name
    # The sample test asks for the page's acknowledgement (PR 6b); its
    # preview changes nothing and asks nothing.
    preview, sample = entries["test sample-preview"], entries["test sample"]
    assert sample["prompts"] and not preview["prompts"]
    assert preview["scope"] == sample["scope"] == "full"
    assert not preview["changes_state"] and preview["audit_event"] is None
    assert sample["changes_state"]
    assert sample["audit_event"] == "admin_cmd_test_sample"
    assert preview["arguments"] == ["REVISION_ID"] and sample["arguments"] == []
    assert sample["result_fields"] == ["created", "request_key", "test"]
    assert preview["result_fields"][-1] == "preview"
    # The chosen-Family send always asks, as its page always does (PR 6c).
    chosen, review = entries["test families"], entries["test families-preview"]
    assert chosen["prompts"] and chosen["changes_state"] and chosen["fresh_gated"]
    assert chosen["audit_event"] == "admin_cmd_test_families"
    assert chosen["result_fields"] == ["created", "request_key", "tickets"]
    # The review changes nothing; its --names export records this (#817).
    assert not review["prompts"] and not review["changes_state"]
    assert review["audit_event"] == "admin_cmd_export_family_test_names"
    options = {option["name"]: option for option in review["options"]}
    assert options["--family"]["required"]
    assert not options["--names"]["takes_value"]
    assert not options["--timezone"]["required"]
    assert review["result_fields"][-1] == "export"
    status = entries["test status"]
    assert status["scope"] == "read_only" and status["audit_event"] is None
    # The manual weekly report asks for the page's acknowledgement (PR 8d).
    assert entries["digest weekly-request"]["prompts"]
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
    # A task retry changes state with a full-scope session, keyed as the
    # page's form is, and records its admin_cmd_* event.
    retry = entries["task retry"]
    assert retry["scope"] == "full" and retry["changes_state"]
    assert retry["request_key"] and not retry["expected_version"]
    assert retry["audit_event"] == "admin_cmd_task_retry"
    assert retry["arguments"] == ["TASK_ID"] and not retry["watch"]
    options = {option["name"]: option for option in retry["options"]}
    assert not options["--request-key"]["required"]
    assert retry["result_fields"] == ["created", "request_key", "task"]
    # A refresh request is keyed as the page's form is and records its
    # admin_cmd_* event; its status is any session's read with no event.
    start, status = entries["refresh start"], entries["refresh status"]
    assert start["scope"] == "full" and start["changes_state"]
    assert start["request_key"] and not start["expected_version"]
    assert start["audit_event"] == "admin_cmd_refresh_start"
    assert start["arguments"] == [] and not start["watch"]
    options = {option["name"]: option for option in start["options"]}
    assert not options["--request-key"]["required"]
    assert start["result_fields"] == ["created", "request_key", "refresh"]
    assert status["scope"] == "read_only" and not status["changes_state"]
    assert status["audit_event"] is None and not status["watch"]
    assert status["result_fields"] == [
        "as_of",
        "running",
        "waiting",
        "lateness_minutes",
        "source",
    ]
    # The delivery reads: any session, the page's view event; resolve is
    # keyed and versioned, with a full-scope session.
    for name in (
        "delivery list",
        "delivery show",
        "delivery refusals",
        "delivery refusal-show",
    ):
        assert entries[name]["scope"] == "read_only", name
        assert not entries[name]["changes_state"], name
        assert entries[name]["audit_event"] == "delivery_viewed", name
    resolve = entries["delivery resolve"]
    assert resolve["scope"] == "full" and resolve["changes_state"]
    assert resolve["request_key"] and resolve["expected_version"]
    assert resolve["audit_event"] == "admin_cmd_delivery_resolve"
    actions = {option["name"]: option for option in resolve["options"]}
    assert "resend" not in actions["--action"]["choices"]
    # The System logs: any session reads the screen; its download, like
    # every export, needs a full-scope session. Each records the page's event.
    listing, export = entries["logs list"], entries["logs export"]
    assert listing["scope"] == "read_only" and export["scope"] == "full"
    assert not listing["changes_state"] and not export["changes_state"]
    assert listing["audit_event"] == "system_logs_viewed"
    assert export["audit_event"] == "system_logs_exported"
    options = {option["name"]: option for option in export["options"]}
    assert options["--format"]["choices"] == ["csv", "jsonl"]
    assert "--through" not in options and "--page" not in options
    # The export lifecycle: status is any session's passive read with
    # --watch and no view event; every other command needs a full-scope
    # session. The four changes are keyed (but cancel) and record their
    # admin_cmd_* event; the download records the page's own event.
    status = entries["export status"]
    assert status["scope"] == "read_only" and not status["changes_state"]
    assert status["watch"] and status["audit_event"] is None
    assert status["arguments"] == ["EXPORT_ID"]
    for verb in ("create", "cancel", "retry", "regenerate"):
        entry = entries[f"export {verb}"]
        assert entry["scope"] == "full" and entry["changes_state"], verb
        assert entry["audit_event"] == f"admin_cmd_export_{verb}", verb
        assert entry["request_key"] == (verb != "cancel"), verb
        assert entry["result_fields"] == ["created", "request_key", "export"]
    create = {option["name"]: option for option in entries["export create"]["options"]}
    assert create["--format"]["choices"] == ["csv", "png", "pdf", "xlsx"]
    assert create["--fact-set"]["required"] and create["--timezone"]["required"]
    download = entries["export download"]
    assert download["scope"] == "full" and not download["changes_state"]
    assert download["audit_event"] == "export_downloaded"
    assert download["arguments"] == ["EXPORT_ID"]
    stream = {option["name"]: option for option in download["options"]}
    assert stream["--stream"]["required"]
    # The aggregate report reads: any session, no state change, the page's
    # own view event (none for the reports root), no campaign and no search.
    events = {
        "report list": None,
        "report participation": "participation_viewed",
        "report responses": "response_dashboard_viewed",
        "report financial": "financial_report_viewed",
        "report talents": "talents_report_viewed",
        "report information": "information_viewed",
        "report ministry": "ministry_report_viewed",
    }
    for name, event in events.items():
        entry = entries[name]
        assert entry["scope"] == "read_only" and not entry["changes_state"], name
        assert entry["audit_event"] == event and not entry["watch"], name
        names = {option["name"] for option in entry["options"]}
        assert not names & {"--campaign", "--search"}, name
    ministry = {
        option["name"]: option for option in entries["report ministry"]["options"]
    }
    assert ministry["--requests"]["choices"] == ["join", "leave"]
    assert "fact_set_id" in entries["report participation"]["result_fields"]
    # The digests: the retained reports are any session's reads with their
    # page's view event; the manual weekly report is a keyed change that
    # needs a full-scope session and --yes, its page's acknowledgement.
    for name, event in (
        ("digest daily", "daily_digest_viewed"),
        ("digest weekly", "weekly_digest_viewed"),
    ):
        entry = entries[name]
        assert entry["scope"] == "read_only" and not entry["changes_state"], name
        assert entry["audit_event"] == event and entry["arguments"] == ["SNAPSHOT_ID"]
    # The Family-level exports: full-scope, keyed changes that create an
    # export record (fetched with export fetch), never a search option.
    for name in (
        "export financial",
        "export information",
        "export ministry",
        "export ministry-packet",
        "export directory",
        "export postal",
    ):
        entry = entries[name]
        assert entry["scope"] == "full" and entry["changes_state"], name
        assert entry["request_key"] and not entry["streams"], name
        assert entry["result_fields"] == ["created", "request_key", "export"]
        options = {option["name"]: option for option in entry["options"]}
        assert options["--format"]["choices"] == ["csv", "xlsx", "pdf"], name
        assert options["--timezone"]["required"] and "--search" not in options
    manual = entries["digest weekly-request"]
    assert manual["scope"] == "full" and manual["changes_state"]
    assert manual["request_key"] and manual["prompts"]
    assert manual["audit_event"] == "admin_cmd_digest_weekly_request"
    assert manual["result_fields"] == ["created", "request_key", "task"]
    assert "--yes" in {option["name"] for option in manual["options"]}
    # resend and refusal-clear: keyed, full scope, asking at the prompt.
    resend = entries["delivery resend"]
    assert resend["scope"] == "full" and resend["changes_state"]
    assert resend["request_key"] and resend["expected_version"]
    assert resend["audit_event"] == "admin_cmd_delivery_resend"
    assert resend["arguments"] == ["MESSAGE_ID"]
    assert resend["result_fields"] == resolve["result_fields"]
    clear = entries["delivery refusal-clear"]
    assert clear["scope"] == "full" and clear["changes_state"]
    assert clear["request_key"] and not clear["expected_version"]
    assert clear["audit_event"] == "admin_cmd_delivery_refusal_clear"
    assert clear["arguments"] == ["REFUSAL_ID"]
    assert clear["result_fields"] == ["created", "request_key", "resolution"]


def test_every_state_change_has_a_registered_described_event():
    """Each admin_cmd_* type is an audit Action with a log description."""
    from parishkit.stewardship.audit.log_descriptions import DESCRIPTIONS
    from parishkit.stewardship.audit.schemas import Action

    events = [
        entry["audit_event"]
        for entry in admin_cli.catalog()
        if (entry["audit_event"] or "").startswith("admin_cmd_")
    ]
    assert events == [
        "admin_cmd_schedule_confirm",
        "admin_cmd_refresh_start",
        "admin_cmd_test_sample",
        # test families-preview's --names export (#817).
        "admin_cmd_export_family_test_names",
        "admin_cmd_test_families",
        "admin_cmd_task_retry",
        "admin_cmd_delivery_resolve",
        "admin_cmd_delivery_resend",
        "admin_cmd_delivery_refusal_clear",
        "admin_cmd_export_create",
        "admin_cmd_export_cancel",
        "admin_cmd_export_retry",
        "admin_cmd_export_regenerate",
        "admin_cmd_export_financial",
        "admin_cmd_export_information",
        "admin_cmd_export_ministry",
        "admin_cmd_export_ministry_packet",
        "admin_cmd_export_directory",
        "admin_cmd_export_postal",
        "admin_cmd_digest_weekly_request",
    ]
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


def stub_admission(monkeypatch, receipts, closed):
    """Replace the host and database admissions around the real ``admitted()``.

    The signing ring this process "loaded" has receipt ``"a" * 64``; the
    running web published ``receipts``. ``closed`` collects each close.
    """
    import contextlib
    from types import SimpleNamespace

    from parishkit.stewardship.deployment import ServiceRole

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
        lambda configuration: receipts,
    )
    monkeypatch.setattr("django.db.connections.close_all", lambda: closed.append(True))


def test_admission_refuses_a_signing_keyring_the_web_did_not_load(monkeypatch):
    """A key rotation in progress (receipts differ) refuses with exit 2."""
    closed = []
    stub_admission(monkeypatch, {"django_signing": "b" * 64}, closed)
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


def run_failing(monkeypatch, handler, *, changes_state=False, load=None, written=None):
    """Run a stand-in command whose handler fails; returns (exit, document).

    Admission is replaced by an empty context, so the handler runs as an
    admitted, session-less command; ``load`` replaces ``load_deployment`` to
    fail before admission instead. The durable log write (#617) is replaced
    too: each context it would insert is appended to ``written``.
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
    monkeypatch.setattr(
        admin_cli, "write_failure", (written if written is not None else []).append
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

    written = []
    with caplog.at_level(logging.DEBUG):
        code, document = run_failing(monkeypatch, failing, written=written)
    assert document["error"]["code"] == expected
    assert len(failure_lines(caplog)) == (expected in admin_cli.TRACED_CODES)
    # Only the unexpected is recorded durably, too (#617).
    assert len(written) == (expected in admin_cli.TRACED_CODES)


def test_an_unexpected_read_error_is_recorded_durably(monkeypatch):
    """#617: one closed context, nothing from the exception but its category."""
    written = []
    code, document = run_failing(monkeypatch, broken, written=written)
    assert code == 3 and document["error"]["code"] == "internal"
    assert written == [
        {
            "failure": "admin_command",
            "failure_kind": "unexpected_failure",
            "command": "probe",
        }
    ]


def test_an_unknown_outcome_is_recorded_as_one(monkeypatch):
    """A change that may have committed says so in its failure word."""

    def committed(args, preamble, runtime, context):
        """Commit, then lose the database."""
        context["committed"] = True
        raise OperationalError(PRIVATE)

    written = []
    code, _ = run_failing(monkeypatch, committed, changes_state=True, written=written)
    assert code == 6
    assert written == [
        {
            "failure": "admin_command_outcome_unknown",
            "failure_kind": "database_unavailable",
            "command": "probe",
        }
    ]


def test_a_failure_before_admission_is_not_recorded_durably(monkeypatch):
    """No admitted database login exists yet; standard error only."""

    def load(path):
        """Fail while loading the configuration."""
        raise AttributeError(PRIVATE)

    written = []
    code, _ = run_failing(monkeypatch, broken, load=load, written=written)
    assert code == 3 and written == []


def test_a_failure_to_record_keeps_the_exit_code(monkeypatch, caplog):
    """A lost durable entry logs one line and never masks the command's outcome."""
    import logging

    def down(context):
        """The database cannot take the entry."""
        raise OperationalError(PRIVATE)

    with caplog.at_level(logging.DEBUG):
        code, document = run_failing(monkeypatch, broken)
        # run_failing installed its own stand-in; replace it and run again.
        monkeypatch.setattr(admin_cli, "write_failure", down)
        caplog.clear()
        out = io.StringIO()
        again = admin_cli.run(
            SimpleNamespace(command_name="probe", config=Path("x")),
            stdin=io.BytesIO(PREAMBLE),
            stdout=out,
            stderr=io.StringIO(),
        )
    assert (code, again) == (3, 3)
    assert json.loads(out.getvalue())["error"] == document["error"]
    lines = [json.loads(line) for line in failure_lines(caplog)]
    assert all(PRIVATE not in json.dumps(line) for line in lines)
    assert [(line["message"], line["level"]) for line in lines] == [
        ("admin_command_failed", "WARNING"),
        ("task_failed", "ERROR"),
    ]
    assert lines[0]["extra"]["failure_kind"] == "database_unavailable"


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
        # The operations commands (PR 9).
        (
            "stub",
            PREAMBLE,
            ["task", "retry", str(uuid4()), "--request-key", str(uuid4())],
            (2, "configuration"),
        ),
        (
            "stub",
            PREAMBLE,
            ["delivery", "list", "--state", "pending"],
            (2, "configuration"),
        ),
        (
            "stub",
            PREAMBLE,
            [
                "delivery",
                "resolve",
                str(uuid4()),
                "--action",
                "note",
                "--expected-version",
                "3",
                "--note",
                "x",
            ],
            (2, "configuration"),
        ),
        # The System logs (PR 8a); logs export shares its module and options.
        (
            "stub",
            PREAMBLE,
            ["logs", "list", "--show", "error", "--sort", "oldest"],
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
        "admitted-task-retry",
        "admitted-delivery-list",
        "admitted-delivery-resolve",
        "admitted-logs-list",
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
        "_report_specs",
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


def test_the_sample_prompt_asks_the_pages_own_acknowledgement():
    """The prompt shows the words the page's checkbox shows, unchanged."""
    from parishkit.stewardship.accounts.campaign_mail_views import CampaignMailForm
    from parishkit.stewardship.admin_tests import UNKNOWN_ACKNOWLEDGEMENT

    label = CampaignMailForm.base_fields["acknowledge_unknown"].label
    assert str(label) == UNKNOWN_ACKNOWLEDGEMENT


def test_the_families_prompt_asks_the_pages_own_acknowledgement():
    """The prompt shows the words the page's checkbox shows, unchanged."""
    from parishkit.stewardship.accounts.campaign_family_test_views import (
        FamilyTestConfirmForm,
    )
    from parishkit.stewardship.admin_tests import FAMILIES_ACKNOWLEDGEMENT

    label = FamilyTestConfirmForm.base_fields["acknowledge"].label
    assert str(label) == FAMILIES_ACKNOWLEDGEMENT


def code_mac_file(tmp_path):
    """The web's code MAC keyring file, and the receipt the web published."""
    from types import SimpleNamespace

    from parishkit.stewardship.accounts.cryptography import CodeMacKeyring, Key
    from parishkit.stewardship.accounts.key_files import (
        serialize_keyring,
        write_private,
    )
    from parishkit.stewardship.accounts.metrics_credentials import (
        credential_receipt,
    )

    root = tmp_path.resolve()
    root.chmod(0o700)
    raw = serialize_keyring(CodeMacKeyring([Key("m1", "active", b"m" * 32)]))
    path = root / "family_code_mac"
    write_private(path, raw)
    return (
        SimpleNamespace(secrets={"family_code_mac": path}),
        {"family_code_mac": credential_receipt(raw, "family_code_mac")},
    )


def test_admission_hands_the_keyring_loader_the_webs_receipts(tmp_path, monkeypatch):
    """The real ``admitted()`` loads no keyring itself; its loader checks the
    receipts the web published at admission (ADM-11 PR 8f)."""
    from types import SimpleNamespace

    from parishkit.stewardship.accounts.cryptography import CodeMacKeyring

    files, receipts = code_mac_file(tmp_path)
    configuration = SimpleNamespace(
        secrets=files.secrets,
        paths={"authority": tmp_path / "authority"},
        public_origin="https://campaign.example.org",
        runtime_budget=None,
    )
    reads = []
    real = admin_cli.load_keyrings
    monkeypatch.setattr(
        admin_cli,
        "load_keyrings",
        lambda config, published, names: (
            reads.append((published, tuple(names))) or real(config, published, names)
        ),
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.authority.AuthorityStore",
        lambda *args: "store",
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.automation_sessions.PairingStore",
        lambda client: "pairing",
    )
    client = SimpleNamespace(connection_pool=SimpleNamespace(disconnect=lambda: None))
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_web.valkey_client", lambda config: client
    )
    closed = []
    published = {"django_signing": "a" * 64, **receipts}
    stub_admission(monkeypatch, published, closed)
    with admin_cli.admitted(configuration) as runtime:
        assert reads == []
        (ring,) = runtime.keyrings(("family_code_mac",))
        assert isinstance(ring, CodeMacKeyring)
        assert reads == [(published, ("family_code_mac",))]
    # A keyring the web did not load (a rotation) refuses, the signing ring fine.
    stub_admission(
        monkeypatch, {"django_signing": "a" * 64, "family_code_mac": "0" * 64}, closed
    )
    with (
        admin_cli.admitted(configuration) as runtime,
        pytest.raises(admin_cli.CredentialMismatch),
    ):
        runtime.keyrings(("family_code_mac",))


def test_an_unreadable_keyring_is_a_credential_mismatch(tmp_path):
    """A ring file that cannot be read or parsed is exit 2, as a rotated one."""
    files, receipts = code_mac_file(tmp_path)
    files.secrets["family_code_mac"].chmod(0o644)
    with pytest.raises(admin_cli.CredentialMismatch):
        admin_cli.load_keyrings(files, receipts, ("family_code_mac",))


def family_key_files(tmp_path):
    """The web's general and public token keyring files, and their receipts."""
    from types import SimpleNamespace

    from parishkit.stewardship.accounts.cryptography import (
        GeneralKeyring,
        Key,
        TokenPrivateKeyring,
    )
    from parishkit.stewardship.accounts.key_files import (
        serialize_keyring,
        write_private,
    )
    from parishkit.stewardship.accounts.metrics_credentials import (
        credential_receipt,
    )

    root = tmp_path.resolve()
    root.chmod(0o700)
    secrets, receipts = {}, {}
    for name, ring in (
        ("general_encryption", GeneralKeyring([Key("g1", "active", b"g" * 32)])),
        (
            "token_public",
            TokenPrivateKeyring([Key("t1", "active", b"t" * 32)]).public(),
        ),
    ):
        raw = serialize_keyring(ring)
        secrets[name] = root / name
        write_private(secrets[name], raw)
        receipts[name] = credential_receipt(raw, name)
    return SimpleNamespace(secrets=secrets), receipts


def test_family_keys_load_only_the_two_rings_the_web_loaded(tmp_path):
    """The general and public token rings, matching the web's receipts (#682)."""
    from parishkit.stewardship.accounts.cryptography import (
        GeneralKeyring,
        TokenPublicKeyring,
    )

    configuration, receipts = family_key_files(tmp_path)
    general, public = admin_cli.load_keyrings(
        configuration, receipts, admin_cli.FAMILY_KEYRINGS
    )
    assert isinstance(general, GeneralKeyring)
    assert isinstance(public, TokenPublicKeyring)
    assert admin_cli.FAMILY_KEYRINGS == ("general_encryption", "token_public")


@pytest.mark.parametrize("name", ["general_encryption", "token_public"])
def test_family_keys_refuse_a_ring_the_web_did_not_load(tmp_path, name):
    """A rotated or unmounted ring is credential_mismatch (exit 2)."""
    configuration, receipts = family_key_files(tmp_path)
    with pytest.raises(admin_cli.CredentialMismatch):
        admin_cli.load_keyrings(
            configuration, {**receipts, name: "0" * 64}, admin_cli.FAMILY_KEYRINGS
        )
    del configuration.secrets[name]
    with pytest.raises(admin_cli.CredentialMismatch):
        admin_cli.load_keyrings(configuration, receipts, admin_cli.FAMILY_KEYRINGS)
    assert admin_cli.EXIT_CODES["credential_mismatch"] == 2


def test_family_keys_refuse_an_unreadable_ring(tmp_path):
    """A ring file that is not a keyring is credential_mismatch, not invalid."""
    from parishkit.stewardship.accounts.key_files import write_private
    from parishkit.stewardship.accounts.metrics_credentials import (
        credential_receipt,
    )

    configuration, receipts = family_key_files(tmp_path)
    bad = b"not a keyring"
    write_private(configuration.secrets["general_encryption"], bad)
    receipts["general_encryption"] = credential_receipt(bad, "general_encryption")
    with pytest.raises(admin_cli.CredentialMismatch):
        admin_cli.load_keyrings(configuration, receipts, admin_cli.FAMILY_KEYRINGS)
        admin_cli.load_family_keys(configuration, receipts)


@pytest.mark.parametrize(
    "template,text",
    [
        ("delivery.html", admin_cli.RESEND_ACKNOWLEDGEMENT),
        ("delivery-refusal.html", admin_cli.CLEAR_ACKNOWLEDGEMENT),
    ],
)
def test_the_prompts_ask_the_pages_own_acknowledgement(template, text):
    """The prompt shows the words the page's checkbox shows, unchanged."""
    from pathlib import Path

    from parishkit.stewardship import accounts

    page = Path(accounts.__file__).parent / "templates" / "stewardship" / template
    assert '{% translate "' + text + '" %}' in page.read_text()
