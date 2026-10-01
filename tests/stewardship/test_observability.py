"""ARC-02: redacted UTC JSONL, safe event context, and isolated correlations."""

import json
import logging
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.observability import (
    DEBUG_LOGGING_VARIABLE,
    CorrelationMiddleware,
    Event,
    SafeJsonFormatter,
    configure_logging,
    correlation,
    debug_swallowed,
    emit,
    redact_secrets,
    request_secrets,
)


@pytest.mark.parametrize(
    "level",
    [logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL],
)
def test_events_keep_safe_context_and_utc(caplog, level):
    """All five levels serialize identifiers and explicit UTC without free text."""
    task_id = uuid4()
    with caplog.at_level(logging.DEBUG), correlation() as identifier:
        emit(Event.TASK_STARTED, level=level, task_id=task_id)
    payload = json.loads(SafeJsonFormatter().format(caplog.records[-1]))
    assert payload["message"] == "task_started"
    assert payload["level"] == logging.getLevelName(level)
    assert payload["extra"] == {
        "task_id": str(task_id),
        "correlation_id": str(identifier),
    }
    assert datetime.fromisoformat(payload["timestamp"]).utcoffset() == UTC.utcoffset(
        None
    )


def test_source_loss_detail_is_logged_only_as_closed_counts(caplog):
    """#320: a refused refresh names the dropped count in ordinary output."""
    with caplog.at_level(logging.DEBUG):
        emit(
            Event.SOURCE_DESTRUCTIVE_CHANGE,
            level=logging.CRITICAL,
            source_loss=("active_head_families", 1200, 3),
        )
    payload = json.loads(SafeJsonFormatter().format(caplog.records[-1]))
    assert payload["extra"]["source_loss"] == {
        "measure": "active_head_families",
        "before": 1200,
        "after": 3,
    }
    for event, loss in (
        (Event.TASK_FAILED, ("family", 2, 1)),
        (Event.SOURCE_DESTRUCTIVE_CHANGE, ("private@example.org", 2, 1)),
        (Event.SOURCE_DESTRUCTIVE_CHANGE, ("family", "2", 1)),
        (Event.SOURCE_DESTRUCTIVE_CHANGE, ("family", 2)),
    ):
        with pytest.raises(ValueError):
            emit(event, source_loss=loss)


def test_source_loss_limit_override_is_logged_only_as_a_percent(caplog):
    """#320: an overridden limit is visible, carried by the reviewed start event."""
    task_id = uuid4()
    with caplog.at_level(logging.DEBUG):
        emit(
            Event.TASK_STARTED,
            level=logging.WARNING,
            task_id=task_id,
            source_max_drop_percent=100,
        )
    payload = json.loads(SafeJsonFormatter().format(caplog.records[-1]))
    assert payload["level"] == "WARNING"
    assert payload["extra"]["source_max_drop_percent"] == 100
    for event, value in (
        (Event.TASK_FAILED, 50),
        (Event.TASK_STARTED, 101),
        (Event.TASK_STARTED, "50"),
        (Event.TASK_STARTED, True),
    ):
        with pytest.raises(ValueError):
            emit(event, source_max_drop_percent=value)


def test_unstructured_errors_are_redacted_without_mutating_record():
    """Discard unsafe names, formatted args, exception text, stacks, and extras."""
    record = logging.LogRecord(
        "synthetic-secret",
        logging.ERROR,
        "sensitive/path",
        1,
        "token=%s",
        ("synthetic-secret",),
        None,
    )
    record.exc_text = "synthetic-secret"
    record.stack_info = "synthetic-secret"
    record.extra = {
        "correlation_id": "synthetic-secret",
        "task_id": None,
        "email": "synthetic-secret",
    }
    output = SafeJsonFormatter().format(record)
    assert "synthetic-secret" not in output and "sensitive/path" not in output
    assert json.loads(output)["message"] == "unstructured_log_suppressed"
    assert json.loads(output)["extra"] == {}
    assert record.getMessage() == "token=synthetic-secret"
    assert record.exc_text == "synthetic-secret"


@pytest.mark.parametrize("extra", [None, "synthetic-secret", ["synthetic-secret"]])
def test_unknown_extra_shapes_are_discarded(extra):
    """Unexpected third-party extras cannot bypass the strict context schema."""
    record = logging.LogRecord(
        "other", logging.INFO, "", 1, Event.CONFIG_REJECTED, (), None
    )
    record.extra = extra
    assert json.loads(SafeJsonFormatter().format(record))["extra"] == {}


def test_nested_correlations_restore_after_error(caplog):
    """Requests and tasks cannot inherit an earlier scope after it has exited."""
    with caplog.at_level(logging.INFO):
        with correlation() as outer:
            with pytest.raises(RuntimeError), correlation():
                raise RuntimeError("synthetic error")
            emit(Event.TASK_COMPLETED)
        emit(Event.TASK_COMPLETED)
    assert caplog.records[-2].extra["correlation_id"] == outer
    assert caplog.records[-1].extra["correlation_id"] is None


def test_invalid_event_inputs_are_rejected():
    """No arbitrary string event, severity, task ID, or correlation ID is allowed."""
    with pytest.raises(ValueError):
        emit("synthetic-secret")
    with pytest.raises(ValueError):
        emit(Event.TASK_STARTED, level=123)
    with pytest.raises(ValueError):
        emit(Event.TASK_STARTED, task_id="synthetic-secret")
    with pytest.raises(ValueError):
        emit(Event.TASK_FAILED, failure_kind="private-value")
    with pytest.raises(ValueError), correlation("synthetic-secret"):
        pass


@pytest.mark.parametrize(
    "kind",
    [
        "database_unavailable",
        "database_write_refused",
        "configuration_unavailable",
        "filesystem_unavailable",
        "unexpected_failure",
    ],
)
def test_installer_failure_keeps_request_identity_and_safe_category(caplog, kind):
    """Distinct operational diagnoses retain no exception text or stack secrets."""
    from django.db import DatabaseError, IntegrityError

    from parishkit.config import ConfigError
    from parishkit.stewardship.observability import installer_request

    error_type = {
        "database_unavailable": DatabaseError,
        "database_write_refused": IntegrityError,
        "configuration_unavailable": ConfigError,
        "filesystem_unavailable": OSError,
        "unexpected_failure": RuntimeError,
    }[kind]
    identifier = uuid4()
    with pytest.raises(error_type), installer_request(identifier):
        raise error_type("private-value-or-path")
    output = SafeJsonFormatter().format(caplog.records[-1])
    assert "private-value-or-path" not in output
    payload = json.loads(output)
    assert payload["message"] == "installer_request_failed"
    assert payload["extra"] == {"correlation_id": str(identifier), "failure_kind": kind}


@pytest.mark.parametrize(
    ("error_type", "sqlstate", "kind"),
    [
        ("IntegrityError", "23514", "database_write_refused"),
        ("ProgrammingError", "42501", "database_write_refused"),
        ("InternalError", "P0001", "database_write_refused"),
        ("OperationalError", "08006", "database_unavailable"),
        ("DatabaseError", None, "database_unavailable"),
    ],
)
def test_guard_refusal_is_not_reported_as_an_outage(caplog, error_type, sqlstate, kind):
    """A guard's refusal, whatever its SQLSTATE, is told apart from an outage."""
    from django import db

    from parishkit.stewardship.observability import emit_failure

    class Cause(Exception):
        """Stands in for the psycopg error Django chains as the cause."""

    cause = Cause()
    cause.sqlstate = sqlstate
    error = getattr(db, error_type)("private-value")
    error.__cause__ = cause
    with caplog.at_level(logging.DEBUG):
        emit_failure(error)
    payload = json.loads(SafeJsonFormatter().format(caplog.records[-1]))
    assert payload["extra"]["failure_kind"] == kind


@pytest.mark.parametrize(
    "supplied", ["synthetic-secret", "3ac12758-abf8-41df-ae3a-9f3c0b43e30a"]
)
def test_client_cannot_supply_correlation_id(client, supplied):
    """Requests get fresh internal IDs, not untrusted tracing-header values."""
    response = client.get("/", HTTP_X_CORRELATION_ID=supplied)
    assert response["X-Correlation-ID"] != supplied
    identifier = UUID(response["X-Correlation-ID"])
    assert identifier != UUID(client.get("/")["X-Correlation-ID"])


@pytest.mark.parametrize(
    "emitter",
    ["", "django", "django.server", "gunicorn.error", "gunicorn.access", "celery"],
)
def test_configured_logging_cannot_bypass_redaction(monkeypatch, capsys, emitter):
    """Replace existing handlers and redact real stderr output from every route."""
    root = logging.getLogger()
    # Protect pytest's capture handlers from setup_logging's close-and-replace
    # behavior, and restore all global logger state when this test ends.
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", logging.DEBUG)
    monkeypatch.setattr(root, "propagate", True)
    monkeypatch.setattr(root, "disabled", False)
    sentinels = []
    children = []
    for name in (
        "django",
        "django.server",
        "gunicorn.error",
        "gunicorn.access",
        "celery",
    ):
        logger = logging.getLogger(name)
        sentinel = logging.StreamHandler()
        monkeypatch.setattr(sentinel, "close", Mock(wraps=sentinel.close))
        monkeypatch.setattr(logger, "handlers", [sentinel])
        monkeypatch.setattr(logger, "propagate", False)
        monkeypatch.setattr(logger, "level", logging.DEBUG)
        monkeypatch.setattr(logger, "disabled", False)
        sentinels.append(sentinel)
        children.append(logger)
    try:
        configure_logging()
        for logger, sentinel in zip(children, sentinels, strict=True):
            assert logger.handlers == []
            assert logger.propagate
            sentinel.close.assert_called_once_with()
        try:
            raise RuntimeError("synthetic-secret-exception")
        except RuntimeError:
            logging.getLogger(emitter).error(
                "synthetic-secret-message=%s",
                "synthetic-secret-argument",
                exc_info=True,
                stack_info=True,
                extra={"extra": {"email": "synthetic-secret-extra"}},
            )
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "synthetic-secret" not in captured.err
        payload = json.loads(captured.err)
        assert payload["message"] == "unstructured_log_suppressed"
        assert payload["level"] == "ERROR"
        assert payload["extra"] == {}
        assert datetime.fromisoformat(
            payload["timestamp"]
        ).utcoffset() == UTC.utcoffset(None)
    finally:
        for handler in root.handlers:
            handler.close()


def test_middleware_restores_context_on_failure(caplog):
    """An uncaught handler failure must still release its per-request context."""

    def fail(request):
        """Inject a failure before a response is available."""
        raise RuntimeError("synthetic error")

    request = type("Request", (), {})()
    with pytest.raises(RuntimeError):
        CorrelationMiddleware(fail)(request)
    with caplog.at_level(logging.INFO):
        emit(Event.CONFIG_REJECTED)
    assert caplog.records[-1].extra["correlation_id"] is None


def _failing_record():
    """An error record with a formatted message and a live exception."""
    try:
        raise RuntimeError("synthetic-detail")
    except RuntimeError:
        import sys

        info = sys.exc_info()
    return logging.LogRecord(
        "synthetic.logger", logging.ERROR, "path", 1, "load %s", ("failed",), info
    )


def test_debug_details_stay_out_unless_explicitly_enabled(monkeypatch):
    """Anything but exactly "1" keeps the redacted production output."""
    for value in (None, "0", "true", "yes"):
        if value is None:
            monkeypatch.delenv(DEBUG_LOGGING_VARIABLE, raising=False)
        else:
            monkeypatch.setenv(DEBUG_LOGGING_VARIABLE, value)
        output = SafeJsonFormatter().format(_failing_record())
        assert "synthetic-detail" not in output
        assert "debug" not in json.loads(output)["extra"]


def test_debug_logging_keeps_message_logger_and_traceback(monkeypatch):
    """Pre-launch debugging shows what failed, alongside the reviewed event."""
    monkeypatch.setenv(DEBUG_LOGGING_VARIABLE, "1")
    output = json.loads(SafeJsonFormatter().format(_failing_record()))
    assert output["message"] == "unstructured_log_suppressed"
    debug = output["extra"]["debug"]
    assert debug["logger"] == "synthetic.logger"
    assert debug["message"] == "load failed"
    assert "RuntimeError: synthetic-detail" in debug["exception"]


TOKEN = "Synthetic-Link-Token_0123456789abcdef"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"/access/{TOKEN}", "/access/[redacted]"),
        (
            f"Service Unavailable: /access/{TOKEN}",
            "Service Unavailable: /access/[redacted]",
        ),
        (
            f'"GET /access/{TOKEN}?x=1 HTTP/1.1" 503',
            '"GET /access/[redacted]?x=[redacted] HTTP/1.1" 503',
        ),
        (
            "/admin/oauth/callback?code=4/0AbC-dEf&state=xyz#frag",
            "/admin/oauth/callback?code=[redacted]&state=[redacted]#frag",
        ),
        ("/admin/login?next=/admin/", "/admin/login?next=[redacted]"),
        # JSON-escaped text: a match stops at the escape, keeping JSON valid.
        (f'{{"m": "/access/{TOKEN}\\"x"}}', '{"m": "/access/[redacted]\\"x"}'),
        ("/admin/campaigns/", "/admin/campaigns/"),
        ("no url here", "no url here"),
        # Raw, undecoded request URIs that Django still routes to the link view.
        (f"GET /access%2F{TOKEN} HTTP/1.1", "GET /access%2F[redacted] HTTP/1.1"),
        (f"GET /%61ccess/{TOKEN} HTTP/1.1", "GET /%61ccess/[redacted] HTTP/1.1"),
        (f"GET %2faccess%2f{TOKEN}", "GET %2faccess%2f[redacted]"),
    ],
)
def test_redact_secrets_keeps_route_and_drops_secret_values(text, expected):
    """Paths keep their route and parameter names; secret values go."""
    assert redact_secrets(text) == expected


def test_redact_secrets_scrubs_literal_values_anywhere():
    """A token outside URL form, as in an exception message, is still removed."""
    assert (
        redact_secrets(f"lookup failed for {TOKEN!r}", (TOKEN,))
        == "lookup failed for '[redacted]'"
    )


def test_request_secrets_take_only_credential_shaped_values():
    """The link token and secret query values count; short or odd values do not."""
    from django.test import RequestFactory

    factory = RequestFactory()
    assert request_secrets(factory.get(f"/access/{TOKEN}")) == (TOKEN,)
    callback = factory.get(
        "/admin/oauth/callback",
        {"code": "4/0AbCdEfGh", "state": "short", "next": "ignored-long-value"},
    )
    assert request_secrets(callback) == ("4/0AbCdEfGh",)
    # Quotes or other punctuation could match JSON structure; never scrub them.
    assert request_secrets(factory.get("/", {"code": '", "message'})) == ()
    assert request_secrets(object()) == ()
    assert request_secrets(None) == ()


def test_django_request_record_never_writes_the_token(monkeypatch):
    """Django's own error record names the path; the formatted line does not."""
    from django.test import RequestFactory

    monkeypatch.setenv(DEBUG_LOGGING_VARIABLE, "1")
    request = RequestFactory().get(f"/access/{TOKEN}")
    record = logging.LogRecord(
        "django.request",
        logging.ERROR,
        "path",
        1,
        "Service Unavailable: %s",
        (request.path,),
        None,
    )
    record.request = request
    output = SafeJsonFormatter().format(record)
    assert TOKEN not in output
    payload = json.loads(output)
    assert payload["extra"]["debug"]["message"] == (
        "Service Unavailable: /access/[redacted]"
    )


def test_middleware_scrubs_its_request_token_from_debug_tracebacks(monkeypatch, caplog):
    """A token inside exception text, logged during the request, is scrubbed."""
    from django.http import HttpResponse
    from django.test import RequestFactory

    monkeypatch.setenv(DEBUG_LOGGING_VARIABLE, "1")
    formatter = SafeJsonFormatter()
    lines = []

    class Capture(logging.Handler):
        """Keep formatted lines while the request's context is still active."""

        def emit(self, record):
            lines.append(formatter.format(record))

    def view(request):
        """Hide a failure whose message embeds the raw token."""
        try:
            raise ValueError(f"bad token {TOKEN}")
        except ValueError:
            debug_swallowed("link lookup failed")
        return HttpResponse(status=503)

    debug = logging.getLogger("parishkit.stewardship.debug")
    handler = Capture()
    caplog.set_level(logging.DEBUG, logger=debug.name)
    debug.addHandler(handler)
    try:
        CorrelationMiddleware(view)(RequestFactory().get(f"/access/{TOKEN}"))
    finally:
        debug.removeHandler(handler)
    assert len(lines) == 1
    assert TOKEN not in lines[0]
    assert "ValueError: bad token [redacted]" in lines[0]


@pytest.mark.parametrize(
    "path",
    [
        # A literal that would split the \u00e9 escape of an encoded "é".
        "/caf%C3%A9abcd?code=u00e9abcd",
        # Literals naming the line's own fields or values.
        "/admin/?state=timestamp",
        "/admin/?code=correlation_id",
        "/admin/?token=unstructured_log_suppressed",
        "/admin/?code=pk.stewardship",
        f"/access/{TOKEN}?state=message_",
    ],
)
def test_attacker_chosen_literals_cannot_break_or_rename_fields(monkeypatch, path):
    """Literal scrubbing touches free text only, never the serialized line."""
    from django.test import RequestFactory

    monkeypatch.setenv(DEBUG_LOGGING_VARIABLE, "1")
    request = RequestFactory().get(path)
    record = logging.LogRecord(
        "django.request",
        logging.ERROR,
        "path",
        1,
        "Service Unavailable: %s (café%s)",
        (request.get_full_path(), "abcd"),
        None,
    )
    record.request = request
    record.extra = {"correlation_id": uuid4()}
    output = SafeJsonFormatter().format(record)
    payload = json.loads(output)
    assert set(payload) >= {"timestamp", "level", "logger", "message", "extra"}
    assert payload["message"] == "unstructured_log_suppressed"
    assert payload["logger"] == "pk.stewardship"
    assert set(payload["extra"]) == {"correlation_id", "debug"}
    UUID(payload["extra"]["correlation_id"])
    datetime.fromisoformat(payload["timestamp"])
    assert set(payload["extra"]["debug"]) == {"logger", "message"}
    assert "(caféabcd)" in payload["extra"]["debug"]["message"]
    assert TOKEN not in output


def test_literals_are_scrubbed_only_inside_a_request(monkeypatch):
    """Outside any request only URL patterns apply; no stale literals linger."""
    from django.http import HttpResponse
    from django.test import RequestFactory

    monkeypatch.setenv(DEBUG_LOGGING_VARIABLE, "1")
    CorrelationMiddleware(lambda request: HttpResponse())(
        RequestFactory().get(f"/access/{TOKEN}")
    )
    record = logging.LogRecord(
        "synthetic", logging.ERROR, "path", 1, "saw %s", (TOKEN,), None
    )
    message = json.loads(SafeJsonFormatter().format(record))["extra"]["debug"]
    assert message["message"] == f"saw {TOKEN}"
