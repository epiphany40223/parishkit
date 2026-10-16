"""``pk-stewardship admin``'s refusal log entries (#1403, PR 6).

The admin-automation spec's Refusal entry: an exit-1 refusal leaves the same
``admin_input_refused`` entry a refused Admin page does, and an exit-2
refusal only its standard-error line. No database: the durable write is
replaced by a list, so these tests see exactly the ``RefusalRecord`` each
refused command builds. The entry it leaves, on its private connection, is
tested against PostgreSQL in
``database/test_admin_input_refusal_log_postgresql.py``.
"""

import contextlib
import io
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.test import RequestFactory
from django.urls import resolve

from parishkit.config import ConfigError
from parishkit.stewardship import admin_cli
from parishkit.stewardship.accounts.admin_editing import error_response
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.accounts.sessions import FreshAuthenticationRequired
from parishkit.stewardship.admin_changes import InvalidChange
from parishkit.stewardship.admin_reads import NotAvailable
from parishkit.stewardship.audit import refusals
from parishkit.stewardship.audit.refusals import RefusalRecord
from parishkit.stewardship.observability import Event, SafeJsonFormatter
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web import refusal_log
from parishkit.stewardship.web.refusal_log import AdminRefusalMiddleware
from parishkit.stewardship.web.refusals import UserFacingDenied, UserFacingError

SECRET = "A" * 43
PREAMBLE = f"pk-admin-session/1 {SECRET} {'f' * 64}\n".encode()
ACTOR = uuid4()
# Text an exception carries, which no entry or line may show.
PRIVATE = "private-value someone@example.org"
ROUTE = "/admin/users/automation/sessions/"


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """No global logging setup, and a fixed mode for every record."""
    monkeypatch.setattr(
        "parishkit.stewardship.observability.configure_logging", lambda: None
    )
    monkeypatch.setattr(refusal_log, "current_mode", lambda: "testing")


@pytest.fixture
def written(monkeypatch):
    """Every RefusalRecord the command line hands to its durable writer."""
    records = []
    monkeypatch.setattr(admin_cli, "write_refusal", records.append)
    return records


def run_refused(monkeypatch, error, *, actor=ACTOR, load=None):
    """Run a stand-in ``probe`` command whose handler raises ``error``.

    Admission is replaced by an empty context, as in ``test_admin_cli``; the
    handler names ``actor`` as ``admit_session`` would. ``load`` replaces
    ``load_deployment`` to fail before admission. Returns (exit, document).
    """

    def handler(args, preamble, runtime, context):
        """Be the session's Administrator, then refuse."""
        context["actor_id"] = actor
        raise error

    spec = admin_cli.CommandSpec(
        name="probe",
        help="A stand-in command.",
        handler=handler,
        scope="none",
        changes_state=False,
        result_fields=(),
        pr=0,
    )
    monkeypatch.setitem(admin_cli.BY_NAME, "probe", spec)
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
    return code, json.loads(out.getvalue())


def refusal_lines(caplog):
    """The formatted ``admin_input_refused`` lines, as the process writes them."""
    return [
        json.loads(SafeJsonFormatter().format(record))
        for record in caplog.records
        if record.msg is Event.ADMIN_INPUT_REFUSED
    ]


def page_record(error, monkeypatch):
    """The RefusalRecord the Admin page's middleware builds for ``error``."""
    records = []
    monkeypatch.setattr(refusal_log, "record_refusal", records.append)
    request = RequestFactory().post(ROUTE)
    request.resolver_match = resolve(ROUTE)
    request.principal = Principal(ACTOR, roles=frozenset({"administrator"}))
    AdminRefusalMiddleware(lambda _: error_response(error))(request)
    (record,) = records
    return record


# Each refusal the page and the command line both answer, with the exit-1
# code the command line gives it and the cleaned context both record.
SHARED = [
    pytest.param(
        ValueError(PRIVATE),
        "invalid",
        {
            "codes": ["invalid"],
            "reason": "unclassified",
            "exception_class": "value_error",
        },
        "INFO",
        id="value-error",
    ),
    pytest.param(
        PermissionError(PRIVATE),
        "denied",
        {
            "codes": ["denied"],
            "reason": "unclassified",
            "exception_class": "permission_error",
        },
        "WARNING",
        id="permission-error",
    ),
    pytest.param(
        FreshAuthenticationRequired(PRIVATE),
        "denied",
        {"codes": ["denied"], "reason": "fresh_authentication_required"},
        "INFO",
        id="step-up",
    ),
    pytest.param(
        UserFacingDenied(PRIVATE),
        "denied",
        {"codes": ["denied"], "reason": "denied"},
        "WARNING",
        id="user-facing-denied",
    ),
    pytest.param(
        StaleRecordError(PRIVATE),
        "stale_version",
        {
            "codes": ["stale_version"],
            "reason": "unclassified",
            "exception_class": "stale_record_error",
        },
        "INFO",
        id="stale",
    ),
    pytest.param(
        UserFacingError(
            PRIVATE,
            reason="campaign_zone_not_parish",
            expected="America/New_York",
            actual="UTC",
        ),
        "invalid",
        {
            "codes": ["invalid"],
            "reason": "campaign_zone_not_parish",
            "expected": "America/New_York",
            "actual": "UTC",
        },
        "WARNING",
        id="setting-reason",
    ),
]


@pytest.mark.parametrize("error,code,context,level", SHARED)
def test_an_exit_1_refusal_is_recorded_as_the_page_records_it(
    monkeypatch, written, error, code, context, level
):
    """Exit 1 with the page's reason, codes and level, the command in place
    of the route, the session's Administrator and the mode; never the
    exception's text."""
    exit_code, document = run_refused(monkeypatch, error)
    assert (exit_code, document["error"]["code"]) == (1, code)
    (record,) = written
    assert record.actor_id == ACTOR
    assert record.context() == {"command": "probe", **context, "mode": "testing"}
    assert record.level == level
    assert PRIVATE not in json.dumps(record.context())
    # The page's own entry for the same refusal differs only in naming its
    # route, method and status rather than the command.
    page = page_record(error, monkeypatch)
    page_context = page.context()
    for key in ("route", "method", "status"):
        page_context.pop(key)
    assert page_context == {
        key: value for key, value in record.context().items() if key != "command"
    }
    assert (page.actor_id, page.level) == (record.actor_id, record.level)


# Fixed ids, one starting with a letter and one with a digit: neither may be
# recorded, whatever its shape.
LETTER_ID = "a1b2c3d4-0000-4000-8000-000000000001"
DIGIT_ID = "9f8e7d6c-0000-4000-8000-000000000002"
# A key a caller could send that fits the identifier shape.
SENT_KEY = "john_smith_555_1234"


def raised(produce):
    """The exception ``produce`` raises."""
    with pytest.raises(Exception) as caught:
        produce()
    return caught.value


def recorded_context(monkeypatch, written, error):
    """Run a command refused with ``error``; its record's cleaned context."""
    exit_code, document = run_refused(monkeypatch, error)
    assert (exit_code, document["error"]["code"]) == (1, "invalid")
    (record,) = written
    context = record.context()
    for secret in (SENT_KEY, LETTER_ID, DIGIT_ID, "tok3nvalue", PRIVATE):
        assert secret not in json.dumps(context)
    return context


def unexpected(count, *codes):
    """The context of an ``unexpected_fields`` refusal of ``count`` keys."""
    return {
        "command": "probe",
        "codes": ["invalid", *codes],
        "reason": "unexpected_fields",
        "unexpected_count": count,
        "mode": "testing",
    }


def test_an_unknown_campaign_settings_key_is_counted_not_named(monkeypatch, written):
    """``campaign.<key>`` for a key the page does not have is counted; a
    known field's problem keeps its own name."""
    from parishkit.stewardship import admin_campaign

    _, booleans, _ = admin_campaign._fields()
    known = sorted(booleans)[0]
    error = raised(
        lambda: admin_campaign.parse_settings(
            json.dumps({"campaign": {SENT_KEY: 1, known: "x"}})
        )
    )
    assert recorded_context(monkeypatch, written, error) == {
        **unexpected(1),
        "fields": [f"campaign-{known}"],
    }


def test_an_unknown_parish_profile_key_is_counted_not_named(monkeypatch, written):
    """``parish.<key>`` for each key the profile does not have."""
    from parishkit.stewardship.admin_parish import parse_profile

    error = raised(
        lambda: parse_profile(json.dumps({"parish": {SENT_KEY: "x", "tok3nvalue": ""}}))
    )
    assert recorded_context(monkeypatch, written, error) == unexpected(2)


def test_an_unknown_integration_setting_is_counted_not_named(monkeypatch, written):
    """``settings.<key>`` for a key the integration's settings do not have."""
    from parishkit.stewardship.admin_integration_changes import parse_settings

    error = raised(
        lambda: parse_settings(
            json.dumps({"settings": {"tok3nvalue": "x"}}), "parishsoft"
        )
    )
    assert recorded_context(monkeypatch, written, error) == unexpected(1)


def test_an_unknown_schedule_id_is_counted_not_named(monkeypatch, written):
    """``schedules.<id>`` for ids no saved schedule has, of either shape."""
    from parishkit.stewardship.admin_changes import form_data

    changes = {"window": {}, "schedules": [{"id": LETTER_ID}, {"id": DIGIT_ID}]}
    error = raised(lambda: form_data(changes, [], None, editable=True, base_digest="x"))
    assert recorded_context(monkeypatch, written, error) == unexpected(2)


def test_a_schedule_form_problem_is_named_without_its_row(monkeypatch, written):
    """The page's form errors keep the form's own field names; a saved
    row's id and an added row's label are left out."""
    from parishkit.stewardship.admin_changes import field_errors

    def form(errors):
        """A validated form stand-in with these ``errors``."""
        return SimpleNamespace(errors=SimpleNamespace(get_json_data=lambda: errors))

    window = form({"start_date": [{"code": "required", "message": PRIVATE}]})
    schedules = SimpleNamespace(
        forms=[
            form({"time": [{"code": "invalid", "message": PRIVATE}]}),
            form({"__all__": [{"code": "invalid", "message": PRIVATE}]}),
        ],
        non_form_errors=lambda: SimpleNamespace(get_json_data=lambda: []),
    )
    problems = field_errors(window, schedules, [LETTER_ID, "new0"])
    # The document still names each row as before.
    assert [item["field"] for item in problems] == [
        "window.start_date",
        f"schedules.{LETTER_ID}.time",
        "schedules.new0",
    ]
    context = recorded_context(monkeypatch, written, InvalidChange(problems))
    assert context == {
        "command": "probe",
        "fields": ["window-start_date", "schedules-time", "schedules"],
        "codes": ["invalid", "required"],
        "reason": "unclassified",
        "exception_class": "invalid_change",
        "mode": "testing",
    }


@pytest.mark.parametrize("file_id", [LETTER_ID, DIGIT_ID], ids=["letter", "digit"])
def test_a_hosted_file_id_is_never_named(monkeypatch, written, file_id):
    """A hosted file problem gives its code only, whatever its id's shape."""
    from parishkit.stewardship.admin_hosted_files import FileRefused, _in_use

    error = FileRefused([_in_use(file_id, [SimpleNamespace(description="A page")])])
    assert recorded_context(monkeypatch, written, error) == {
        "command": "probe",
        "codes": ["invalid"],
        "reason": "unclassified",
        "exception_class": "file_refused",
        "mode": "testing",
    }


def test_a_plain_problem_gives_its_code_only(monkeypatch, written):
    """A problem no command marked keeps no name, however it looks."""

    error = InvalidChange([{"field": SENT_KEY, "code": "required", "message": "x"}])
    assert recorded_context(monkeypatch, written, error) == {
        "command": "probe",
        "codes": ["invalid", "required"],
        "reason": "unclassified",
        "exception_class": "invalid_change",
        "mode": "testing",
    }


def test_not_available_is_an_info_refusal(monkeypatch, written):
    """An unknown task or campaign: exit 1, INFO, recorded once."""
    exit_code, document = run_refused(monkeypatch, NotAvailable())
    assert (exit_code, document["error"]["code"]) == (1, "not_available")
    (record,) = written
    assert record.context()["codes"] == ["not_available"]
    assert record.level == "INFO"


@pytest.mark.parametrize(
    "error,code,exit_code",
    [
        (RuntimeError(PRIVATE), "internal", 3),
        (admin_cli.ConfirmationRequired(), "confirmation_required", 4),
    ],
    ids=["internal", "confirmation"],
)
def test_other_exits_are_not_refusals_of_input(
    monkeypatch, caplog, written, error, code, exit_code
):
    """An unexpected error or a declined confirmation records no refusal."""
    monkeypatch.setattr(admin_cli, "write_failure", lambda context: None)
    with caplog.at_level(logging.INFO):
        result, document = run_refused(monkeypatch, error)
    assert (result, document["error"]["code"]) == (exit_code, code)
    assert written == [] and refusal_lines(caplog) == []


def test_a_usage_error_has_its_line_only(caplog):
    """Exit 2 from the parser: one INFO line, reason ``usage``, no command,
    no actor and none of the arguments; then the usual document."""
    out = io.StringIO()
    with caplog.at_level(logging.INFO):
        code = admin_cli.main(
            ["task", "show", PRIVATE, "--config", "x", "--session-stdin"],
            stdin=io.BytesIO(PREAMBLE),
            stdout=out,
            stderr=out,
        )
    assert code == 2 and json.loads(out.getvalue())["error"]["code"] == "usage"
    (line,) = refusal_lines(caplog)
    assert line["level"] == "INFO"
    assert line["extra"]["refusal"] == {"reason": "usage"}
    assert "actor_id" not in line["extra"] and PRIVATE not in json.dumps(line)


def test_a_configuration_error_before_admission_has_its_line_only(monkeypatch, caplog):
    """Exit 2 before admission: a WARNING line naming the command and no
    actor, and no durable entry."""

    def load(path):
        """Refuse the configuration."""
        raise ConfigError(PRIVATE)

    opened = []
    monkeypatch.setattr(
        "parishkit.stewardship.audit.timeouts._private_connection", opened.append
    )
    with caplog.at_level(logging.INFO):
        code, document = run_refused(monkeypatch, ValueError(), load=load)
    assert (code, document["error"]["code"]) == (2, "configuration")
    (line,) = refusal_lines(caplog)
    assert line["level"] == "WARNING"
    assert line["extra"]["refusal"] == {"command": "probe", "reason": "configuration"}
    assert "actor_id" not in line["extra"] and PRIVATE not in json.dumps(line)
    # No actor, so no private connection was opened.
    assert opened == []


def test_the_writer_uses_a_private_connection_and_closes_it(monkeypatch):
    """The command's connections are closed first, the record goes to
    ``record_refusal`` with a private connection, and that is closed."""
    events = []
    private = SimpleNamespace(close=lambda: events.append("closed"))
    monkeypatch.setattr(
        "django.db.connections.close_all", lambda: events.append("close_all")
    )
    monkeypatch.setattr(
        "parishkit.stewardship.audit.timeouts._private_connection",
        lambda settings: events.append("opened") or private,
    )
    monkeypatch.setattr(
        refusals,
        "record_refusal",
        lambda record, *, db, durable: events.append(
            ("recorded", record.actor_id, db, durable)
        ),
    )
    admin_cli.write_refusal(RefusalRecord(reason="denied", actor_id=ACTOR))
    assert events == [
        "close_all",
        "opened",
        ("recorded", ACTOR, private, True),
        "closed",
    ]


def test_no_private_connection_still_leaves_the_line(monkeypatch, caplog):
    """A connection that cannot be made logs why; the refusal still has its
    line, naming its actor, and no durable write is tried."""

    def unavailable(settings):
        """No connection can be built."""
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr(
        "parishkit.stewardship.audit.timeouts._private_connection", unavailable
    )
    with caplog.at_level(logging.INFO):
        admin_cli.write_refusal(
            RefusalRecord(reason="denied", actor_id=ACTOR, command="probe")
        )
    # First why nothing durable was tried (its category only), then the
    # refusal's own line, still naming the actor.
    failure, line = refusal_lines(caplog)
    assert failure["extra"] == {"failure_kind": "unexpected_failure"}
    assert line["level"] == "WARNING"
    assert line["extra"]["refusal"] == {"command": "probe", "reason": "denied"}
    assert line["extra"]["actor_id"] == str(ACTOR) and PRIVATE not in caplog.text


def test_a_scope_denial_names_the_session_administrator(monkeypatch):
    """A read-only session running a full-scope command is refused after the
    session's Administrator is known, so its entry names them."""
    from parishkit.stewardship.accounts import admin_caller, automation_sessions

    caller = SimpleNamespace(
        read_only=True,
        principal=SimpleNamespace(identity=ACTOR),
        portal_session=object(),
    )
    monkeypatch.setattr(
        admin_caller.AdminCaller, "from_automation", lambda *a, **k: caller
    )
    monkeypatch.setattr(
        automation_sessions, "close_command_session", lambda session: None
    )
    spec = SimpleNamespace(scope="full", changes_state=False)
    runtime = SimpleNamespace(store=None, pairing=None)
    context = {}
    with pytest.raises(PermissionError):
        admin_cli.admit_session(
            spec, SimpleNamespace(secret="", host_digest=""), runtime, None, context
        )
    assert context["actor_id"] == ACTOR


def no_private_connection(monkeypatch):
    """Record each private connection the writer would open (none expected)."""
    opened = []
    monkeypatch.setattr(
        "parishkit.stewardship.audit.timeouts._private_connection", opened.append
    )
    return opened


def test_a_usage_error_after_admission_never_names_the_actor(monkeypatch, caplog):
    """A command's own argument check after admission (exit 2): one INFO
    line naming the command, no actor even though one is known, and no
    durable write."""
    opened = no_private_connection(monkeypatch)
    with caplog.at_level(logging.INFO):
        code, document = run_refused(monkeypatch, admin_cli.UsageError(PRIVATE))
    assert (code, document["error"]["code"]) == (2, "usage")
    (line,) = refusal_lines(caplog)
    assert line["level"] == "INFO"
    assert line["extra"]["refusal"] == {"command": "probe", "reason": "usage"}
    assert "actor_id" not in line["extra"] and PRIVATE not in json.dumps(line)
    assert opened == []


def test_an_exit_1_refusal_with_no_actor_has_its_line_only(monkeypatch, caplog):
    """No session's Administrator is known: the line names no actor and no
    durable write is tried."""
    opened = no_private_connection(monkeypatch)
    with caplog.at_level(logging.INFO):
        code, document = run_refused(monkeypatch, PermissionError(PRIVATE), actor=None)
    assert (code, document["error"]["code"]) == (1, "denied")
    (line,) = refusal_lines(caplog)
    assert line["level"] == "WARNING" and "actor_id" not in line["extra"]
    assert line["extra"]["refusal"]["command"] == "probe"
    assert opened == []


def test_a_failure_to_record_keeps_the_document_and_exit_code(
    monkeypatch, caplog, written
):
    """Building the record fails: one WARNING failure line, and the
    command's document and exit code are those of the refusal."""

    def broken(*args, **kwargs):
        """The record cannot be built."""
        raise RuntimeError(PRIVATE)

    expected = run_refused(monkeypatch, ValueError(PRIVATE))
    monkeypatch.setattr(admin_cli, "refusal_record", broken)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        code, document = run_refused(monkeypatch, ValueError(PRIVATE))
    assert code == expected[0] == 1
    assert document["error"] == expected[1]["error"]
    (line,) = refusal_lines(caplog)
    assert line["level"] == "WARNING"
    assert line["extra"] == {
        "correlation_id": document["correlation_id"],
        "failure_kind": "unexpected_failure",
    }
    # Only the first run reached the writer.
    assert len(written) == 1 and PRIVATE not in caplog.text
