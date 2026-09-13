"""Redacted operational logging before campaign-specific audit schemas land."""

import copy
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from parishkit.logging import JsonLogFormatter, log_extra, setup_logging


class Event(StrEnum):
    """Reviewed event names; arbitrary user text is never an operational message."""

    CONFIG_REJECTED = "configuration_rejected"
    CONFIG_MISMATCH = "configuration_digest_mismatch"
    STARTUP_REJECTED = "startup_rejected"
    STARTUP_VALIDATED = "startup_validated"
    REQUEST_COMPLETED = "request_completed"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    SOURCE_INVALID = "source_refresh_invalid"
    SOURCE_HELD = "source_refresh_held"
    SOURCE_CREDENTIAL_FAILED = "source_credential_failed"
    SOURCE_PROVIDER_FAILED = "source_provider_failed"
    INSTALLER_REQUEST_FAILED = "installer_request_failed"
    AUTHENTICATION_LIMITS_WEAKENED = "authentication_limits_weakened"
    UNSTRUCTURED = "unstructured_log_suppressed"


class FailureKind(StrEnum):
    """Safe operational categories, never exception text or credential values."""

    DATABASE = "database_unavailable"
    CREDENTIAL = "credential_unavailable"
    CONFIGURATION = "configuration_unavailable"
    FILESYSTEM = "filesystem_unavailable"
    UNEXPECTED = "unexpected_failure"


_correlation: ContextVar[UUID | None] = ContextVar(
    "stewardship_correlation", default=None
)


def current_correlation() -> UUID:
    """Reuse the bound request/task ID; create an ID for an unscoped operation."""
    return _correlation.get() or uuid4()


@contextmanager
def correlation(identifier: UUID | None = None):
    """Bind an internal correlation UUID, restoring the caller's scope on exit."""
    if identifier is not None and not isinstance(identifier, UUID):
        raise ValueError("correlation must be an internal UUID")
    value = uuid4() if identifier is None else identifier
    token = _correlation.set(value)
    try:
        yield value
    finally:
        _correlation.reset(token)


def emit(
    event: Event,
    *,
    level: int = logging.INFO,
    task_id: UUID | None = None,
    authentication_limits: tuple[str, ...] = (),
    failure_kind: FailureKind | None = None,
) -> None:
    """Emit only typed identifiers and an allowlisted event; accept no free text."""
    if not isinstance(event, Event) or level not in {
        logging.DEBUG,
        logging.INFO,
        logging.WARNING,
        logging.ERROR,
        logging.CRITICAL,
    }:
        raise ValueError("event and severity must be recognized logging values")
    if task_id is not None and not isinstance(task_id, UUID):
        raise ValueError("task_id must be an internal UUID")
    if failure_kind is not None and not isinstance(failure_kind, FailureKind):
        raise ValueError("Failure categories must be reviewed values.")
    if authentication_limits and (
        event is not Event.AUTHENTICATION_LIMITS_WEAKENED
        or not _safe_thresholds(authentication_limits)
    ):
        raise ValueError("Authentication threshold names must be reviewed fields.")
    logging.getLogger("parishkit.stewardship").log(
        level,
        event,
        extra=log_extra(
            {
                "correlation_id": _correlation.get(),
                "task_id": task_id,
                "authentication_limits": authentication_limits,
                "failure_kind": failure_kind,
            }
        ),
    )


def emit_failure(error, *, event=Event.TASK_FAILED):
    """Classify a failure without serializing any exception-controlled field."""
    from django.db import DatabaseError

    from parishkit.config import ConfigError

    from .accounts.credential_errors import CredentialValidationUnavailable
    from .accounts.cryptography import CryptographicError

    kind = next(
        (
            kind
            for cls, kind in (
                (DatabaseError, FailureKind.DATABASE),
                (CredentialValidationUnavailable, FailureKind.CREDENTIAL),
                (CryptographicError, FailureKind.CREDENTIAL),
                (ConfigError, FailureKind.CONFIGURATION),
                (OSError, FailureKind.FILESYSTEM),
            )
            if isinstance(error, cls)
        ),
        FailureKind.UNEXPECTED,
    )
    emit(event, level=logging.ERROR, failure_kind=kind)


@contextmanager
def installer_request(identifier):
    """Correlate a selected durable request's failures before unwinding its scope."""
    with correlation(identifier):
        try:
            yield
        except Exception as error:
            emit_failure(error, event=Event.INSTALLER_REQUEST_FAILED)
            raise


def _safe_thresholds(value):
    """Accept only names from the typed policy, never arbitrary metadata strings."""
    from dataclasses import fields

    from .authentication_policy import AuthenticationLimits

    names = {item.name for item in fields(AuthenticationLimits)}
    return (
        type(value) is tuple
        and 0 < len(value) <= len(names)
        and all(type(item) is str and item in names for item in value)
    )


class SafeJsonFormatter(JsonLogFormatter):
    """Reuse ParishKit JSONL shape while dropping unsafe messages and context.

    Copy the record so redaction does not mutate what another handler receives.
    A safe formatting boundary is necessary even for third-party request errors:
    URLs, query strings, exception messages, and stack locals may hold secrets.
    """

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        """Record an explicit UTC instant rather than inheriting the host timezone."""
        return datetime.fromtimestamp(record.created, UTC).isoformat()

    def format(self, record: logging.LogRecord) -> str:
        """Keep only reviewed event enums and strictly typed safe extra fields."""
        safe = copy.copy(record)
        safe.name = "parishkit.stewardship"
        safe.msg = (
            record.msg.value
            if isinstance(record.msg, Event)
            else Event.UNSTRUCTURED.value
        )
        safe.args = ()
        safe.exc_info = None
        safe.exc_text = None
        safe.stack_info = None
        context = getattr(record, "extra", {})
        safe.extra = {
            key: str(context[key])
            for key in ("correlation_id", "task_id")
            if isinstance(context, dict) and isinstance(context.get(key), UUID)
        }
        if isinstance(context, dict) and isinstance(
            context.get("failure_kind"), FailureKind
        ):
            safe.extra["failure_kind"] = context["failure_kind"].value
        if (
            record.msg is Event.AUTHENTICATION_LIMITS_WEAKENED
            and isinstance(context, dict)
            and _safe_thresholds(context.get("authentication_limits"))
        ):
            safe.extra["authentication_limits"] = list(context["authentication_limits"])
        return super().format(safe)


def configure_logging(config: dict | None = None) -> None:
    """Install redacted JSONL on stderr via shared logging; no provider handlers."""
    logger = setup_logging(verbose=True)
    for handler in logger.handlers:
        handler.setFormatter(SafeJsonFormatter())
    # Django installs its own console/server handlers before calling this hook.
    # Remove those parallel outputs or token-bearing request paths could bypass
    # our root formatter. Server/worker entrypoints must preserve this routing.
    for name in (
        "django",
        "django.server",
        "gunicorn.error",
        "gunicorn.access",
        "celery",
    ):
        child = logging.getLogger(name)
        for handler in child.handlers:
            handler.close()
        child.handlers.clear()
        child.propagate = True


class CorrelationMiddleware:
    """Assign internal per-request IDs; never trust a browser-supplied trace ID."""

    def __init__(self, get_response):
        """Retain the next synchronous Django handler."""
        self.get_response = get_response

    def __call__(self, request):
        """Restore context even when a downstream handler raises an exception."""
        with correlation() as identifier:
            request.correlation_id = identifier
            response = self.get_response(request)
            response["X-Correlation-ID"] = str(identifier)
            return response
