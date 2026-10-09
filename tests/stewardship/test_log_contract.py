"""Every WARNING-or-above log entry says exactly what went wrong (#633).

The Administrator's rule, checked four ways: ``operational`` refuses a serious
entry without its required context; every Python call site and every SQL
producer of a serious entry carries the keys ``audit.log_contract.REQUIRED``
names for its event; every serious process-log line carries a detail; and the
closed vocabularies the detail uses are mirrored exactly in SQL.
"""

import ast
import re
from datetime import timedelta
from pathlib import Path

import pytest

from parishkit.stewardship.audit import log_contract
from parishkit.stewardship.audit.log_contract import REQUIRED, SERIOUS, missing
from parishkit.stewardship.audit.log_details import FAILURE_TEXT, REASON_TEXT
from parishkit.stewardship.audit.schemas import (
    FAILURES,
    FIELDS,
    REASONS,
    ContextKind,
    Outcome,
    sanitize,
)
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.jobs.due_work_health import FALLBACK_CONTEXT, DueWorkScan
from parishkit.stewardship.observability import Event, FailureKind

from .log_samples import SAMPLES, sample

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src/parishkit/stewardship"
SCHEMA = PACKAGE / "schema"
SERIOUS_LOGGING = {"WARNING", "ERROR", "CRITICAL"}
# The process-log details ``observability.emit`` accepts besides the level.
EMIT_DETAILS = {
    "task_id",
    "authentication_limits",
    "failure_kind",
    "source_loss",
    "source_max_drop_percent",
    "drive_failure",
    "timeout",
    "limit_seconds",
    "elapsed_seconds",
    "ministry_duid",
    "shaping",
    "error_class",
}
# Call sites whose context is not a literal, and why each still meets the
# contract (each named function is checked below).
BUILT_CONTEXTS = {
    # Every settled ParishSoft read failure (source.failures.failure_context).
    "failure_context",
}


# Call sites whose event comes from a table, and the events it can hold.
def _health_check_events():
    """The events a failed operational-intake health check can record."""
    from parishkit.stewardship.jobs.operational_collection import HEALTH_CHECKS

    return {event for event, _ in HEALTH_CHECKS.values()}


TABLE_EVENTS = {"jobs/operational_collection.py": _health_check_events}
OPAQUE_CONTEXTS = {
    # A Member source diagnostic: the error's own member_source context,
    # which sanitize refuses unless it holds all three keys.
    "responses/diagnostics.py": "error.context",
}


def test_every_serious_event_has_a_complete_sample():
    """Each contract row is satisfiable within its schema's closed fields."""
    assert SAMPLES.keys() == REQUIRED.keys()
    for event, (schema, context) in SAMPLES.items():
        assert not missing(event, "CRITICAL", context), event
        assert REQUIRED[event] <= FIELDS[schema], event
        sanitize(schema, dict(context))


def test_operational_refuses_a_serious_entry_that_says_nothing():
    """The check runs before any database write, so no database is needed."""
    for level in SERIOUS:
        with pytest.raises(ValueError, match="what went wrong"):
            operational(Event.TASK_FAILED, level=level)
        with pytest.raises(ValueError, match="what went wrong"):
            operational(
                Event.TASK_FAILED,
                level=level,
                schema=ContextKind.FAILURE,
                context={"failure": "alert_mail"},
            )
        # An event with no contract row may not be serious at all.
        with pytest.raises(ValueError, match="what went wrong"):
            operational(
                Event.REQUEST_COMPLETED, level=level, **sample(Event.TASK_FAILED)
            )


def test_production_writes_an_incomplete_entry_and_logs_the_miss(
    settings, monkeypatch, caplog
):
    """A failure record never becomes a failure itself outside the tests."""
    from parishkit.stewardship.audit import services
    from parishkit.stewardship.audit.log_contract import enforce

    settings.STEWARDSHIP_LOG_CONTRACT_STRICT = False
    written = []
    monkeypatch.setattr(
        services.OperationalLog.objects,
        "create",
        lambda **fields: written.append(fields) or fields,
    )
    entry = operational(
        Event.TASK_FAILED,
        level="CRITICAL",
        schema=ContextKind.FAILURE,
        context={"outcome": Outcome.FAILED},
    )
    assert entry["event"] == "task_failed" and entry["level"] == "CRITICAL"
    assert entry["context"] == {"outcome": "failed"}
    (line,) = [r for r in caplog.records if r.msg is Event.TASK_FAILED]
    assert line.levelname == "WARNING"
    assert line.extra["failure_kind"] is FailureKind.LOG_CONTRACT_INCOMPLETE
    # A complete entry logs nothing extra; an unregistered event still writes.
    caplog.clear()
    assert enforce(Event.TASK_FAILED, "ERROR", sample(Event.TASK_FAILED)["context"])
    assert not caplog.records
    assert operational(Event.REQUEST_COMPLETED, level="ERROR")["event"] == (
        "request_completed"
    )
    assert len(written) == 2


def test_the_test_settings_make_a_miss_fail():
    from django.conf import settings

    assert settings.STEWARDSHIP_LOG_CONTRACT_STRICT is True


def test_entries_below_warning_have_no_requirement():
    for level in ("DEBUG", "INFO"):
        assert missing(Event.TASK_FAILED, level, {}) == frozenset()
        assert missing(Event.REQUEST_COMPLETED, level, None) == frozenset()


def _literal_level(node):
    """A call's ``level=`` as a string, or None when it is not a literal."""
    for keyword in node.keywords:
        if keyword.arg == "level":
            value = keyword.value
            if isinstance(value, ast.Constant):
                return value.value
            if (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "logging"
            ):
                return value.attr
            return None
    return "DEFAULT"


def _events(node):
    """The Event members a call's first argument can name, or None if dynamic."""
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"Event", "LogEvent"}
    ):
        return {Event[node.attr]}
    if isinstance(node, ast.IfExp):
        body, orelse = _events(node.body), _events(node.orelse)
        return None if body is None or orelse is None else body | orelse
    return None


def _calls(name):
    """Every call to ``name`` in the package source, as (path, node)."""
    for path in sorted(PACKAGE.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            called = getattr(function, "id", None) or getattr(function, "attr", None)
            if called == name:
                yield path.relative_to(PACKAGE).as_posix(), node


def test_every_python_producer_of_a_serious_entry_names_its_context():
    """Each ``operational`` call at WARNING or above carries its event's keys."""
    seen = 0
    for path, node in _calls("operational"):
        if path.startswith("audit/services.py") or not node.args:
            continue
        level = _literal_level(node)
        if level in {"DEBUG", "INFO", "DEFAULT"}:
            continue
        seen += 1
        context = next((k.value for k in node.keywords if k.arg == "context"), None)
        assert context is not None, f"{path}:{node.lineno} records no context"
        events = _events(node.args[0])
        if events is None and path in TABLE_EVENTS:
            events = TABLE_EVENTS[path]()
        if isinstance(context, ast.Dict):
            keys = {key.value for key in context.keys if isinstance(key, ast.Constant)}
            assert events is not None, f"{path}:{node.lineno} names no event"
            for event in events:
                assert event in REQUIRED, f"{path}:{node.lineno}: {event} unregistered"
                assert REQUIRED[event] <= keys, f"{path}:{node.lineno}: {event}"
        elif isinstance(context, ast.Call):
            assert getattr(context.func, "id", None) in BUILT_CONTEXTS, path
        else:
            assert OPAQUE_CONTEXTS.get(path) == ast.unparse(context), path
    assert seen >= 12


def test_built_failure_contexts_meet_every_source_events_contract():
    """``failure_context`` is the context of every settled source read failure."""
    from types import SimpleNamespace
    from uuid import uuid4

    from parishkit.stewardship.source.failures import ReadFailure, failure_context

    result = SimpleNamespace(run_id=uuid4(), version=3)
    for event in (
        Event.SOURCE_PROVIDER_FAILED,
        Event.SOURCE_CREDENTIAL_FAILED,
        Event.SOURCE_HELD,
        Event.SOURCE_INVALID,
        Event.SOURCE_TENANT_MISMATCH,
        Event.SOURCE_DESTRUCTIVE_CHANGE,
    ):
        for action in ("retryable_failure", "permanent_failure"):
            decision = ReadFailure(True, False, event, "provider_status", status=502)
            context = failure_context(decision, result, attempt=2, action=action)
            assert not missing(event, "CRITICAL", context), (event, action)
            sanitize(ContextKind.FAILURE, context)


def test_late_work_contexts_meet_the_contract():
    """The due-work trigger copies the scan's context; it must be complete."""
    scan = DueWorkScan()
    scan._late("outbox_delivery", timedelta(minutes=17))
    assert not missing(Event.DUE_WORK_LAG, "CRITICAL", scan.details())
    assert not missing(Event.DUE_WORK_LAG, "CRITICAL", DueWorkScan().details())
    assert not missing(Event.DUE_WORK_LAG, "CRITICAL", FALLBACK_CONTEXT)


def _functions(text):
    """Each SQL function's name and definition text in one schema file."""
    for match in re.finditer(r"^CREATE FUNCTION (?:public\.)?(\w+)\(", text, re.M):
        quote = re.compile(r"\bAS\s+(\$\w*\$)").search(text, match.end())
        end = text.index(quote[1] + ";", quote.end())
        yield match[1], text[match.start() : end]


# The SQL producer whose context is built in Python (the due-work scan,
# checked above) and copied in through a transaction-local setting, with the
# same minimal fallback when that setting is missing or refused.
SQL_FROM_PYTHON = {"stewardship_due_work_health_v1"}


def test_every_sql_producer_of_a_serious_entry_names_its_context():
    """Each trigger that writes WARNING or above writes its event's keys.

    The fresh-install files hold every current body; a producer replaced by a
    migration equals its baseline copy (test_schema_migration_files).
    """
    values = {event.value: event for event in Event}
    found = set()
    for path in sorted(SCHEMA.glob("*.sql")):
        for name, body in _functions(path.read_text(encoding="utf-8")):
            for insert in re.finditer(
                r"INSERT INTO (?:public\.)?stewardship_operational_log", body
            ):
                statement = body[insert.start() : body.index(";", insert.end())]
                # Words compared against (NEW.outcome='...') are not written.
                literals = set(re.findall(r"(?<![=<>])'(\w+)'", statement))
                levels = literals & SERIOUS_LOGGING
                if not levels:
                    continue
                events = {values[word] for word in literals if word in values}
                assert len(events) == 1, (path.name, name, events)
                (event,) = events
                found.add((name, event))
                assert event in REQUIRED, (name, event)
                assert "'{}'" not in statement, f"{name} writes an empty context"
                if name in SQL_FROM_PYTHON:
                    # The context comes from Python, but a missing or
                    # refused one must fall back to the same minimal
                    # context, never an empty one (#633).
                    assert not re.search(r":=\s*'\{\}'", body), name
                    fallback = "jsonb_build_object('limit_seconds',{})".format(
                        FALLBACK_CONTEXT["limit_seconds"]
                    )
                    assert fallback in body, name
                written = set(re.findall(r"'(\w+)'", body))
                assert REQUIRED[event] <= written, (name, event)
    assert {
        ("stewardship_mail_health_result_v1", Event.MAIL_PROVIDER_FAILED),
        ("stewardship_ops_delivery_error_v1", Event.TASK_FAILED),
        ("stewardship_security_delivery_error_v1", Event.TASK_FAILED),
        ("stewardship_ops_slack_error_v1", Event.TASK_FAILED),
        ("stewardship_ops_slack_task_error_v1", Event.TASK_FAILED),
        ("stewardship_delivery_warning_v1", Event.DELIVERY_UNKNOWN),
        ("stewardship_cleanup_failure_v1", Event.PRODUCTION_CLEANUP_FAILED),
        ("stewardship_due_work_health_v1", Event.DUE_WORK_LAG),
    } <= found


# Serious process-log events whose name is the whole message, so they carry
# no detail by design. Each must be a fixed state with nothing more to say:
# adding one needs a reason here, never just a missing detail.
SELF_DESCRIBING = {
    # The process started with PARISHKIT_DEBUG_LOGGING=1 (#546); the switch
    # has no other value to report, and free text is what it warns about.
    Event.DEBUG_LOGGING_ENABLED,
}


def test_every_serious_process_log_line_carries_a_detail():
    """An ``emit`` at WARNING or above names a category, task, limit or count.

    Every module is checked, observability itself included; only an event in
    SELF_DESCRIBING may go without a detail, and each of those must still be
    emitted somewhere, so the allowlist cannot outlive its event.
    """
    seen = 0
    described = set()
    for path, node in _calls("emit"):
        if _literal_level(node) not in SERIOUS_LOGGING:
            continue
        seen += 1
        given = {keyword.arg for keyword in node.keywords}
        if given & EMIT_DETAILS:
            continue
        events = _events(node.args[0]) if node.args else None
        assert events and events <= SELF_DESCRIBING, (
            f"{path}:{node.lineno} says only its event"
        )
        described |= events
    assert seen >= 20
    assert described == SELF_DESCRIBING


def _sql_list(key):
    """The quoted words a key's branch of stewardship_safe_context_v1 allows."""
    text = (SCHEMA / "functions.sql").read_text(encoding="utf-8")
    start = text.index(f"ELSIF key='{key}' THEN")
    end = text.index("THEN RETURN false", start + len(key) + 20)
    return set(re.findall(r"'(\w+)'", text[start:end])) - {key, "string"}


def _sql_schema(name):
    """The keys stewardship_safe_context_v1 allows for one schema."""
    text = (SCHEMA / "functions.sql").read_text(encoding="utf-8")
    start = text.index(f"WHEN '{name}' THEN ARRAY[")
    end = text.index("]", start)
    return set(re.findall(r"'(\w+)'", text[start + len(name) + 3 : end]))


def test_closed_vocabularies_are_mirrored_in_sql():
    assert _sql_list("failure") == FAILURES
    assert _sql_list("reason") == REASONS
    for kind in (ContextKind.FAILURE, ContextKind.RECOVERY, ContextKind.DUE_WORK):
        assert _sql_schema(kind.value) == FIELDS[kind], kind


def test_every_failure_and_reason_has_plain_words():
    assert FAILURE_TEXT.keys() == FAILURES
    assert REASON_TEXT.keys() == REASONS


def test_the_contract_covers_only_reviewed_levels():
    assert {"WARNING", "ERROR", "CRITICAL"} == log_contract.SERIOUS


def test_the_recovery_trigger_leaves_out_exactly_the_automation_kinds():
    """Automation notices end without recovering, so they get no entry (#633)."""
    from parishkit.stewardship.jobs.operational_content import AUTOMATION_KINDS

    text = (SCHEMA / "migrations/0008_log_detail.sql").read_text(encoding="utf-8")
    start = text.index("CREATE TRIGGER stewardship_ops_incident_recovered")
    clause = text[start : text.index("EXECUTE FUNCTION", start)]
    left_out = set(re.findall(r"'(\w+)'", clause))
    assert left_out == {kind.value for kind in AUTOMATION_KINDS}
