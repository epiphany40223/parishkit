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
    UNSTRUCTURED = "unstructured_log_suppressed"


_correlation: ContextVar[UUID | None] = ContextVar(
    "stewardship_correlation", default=None
)


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
    event: Event, *, level: int = logging.INFO, task_id: UUID | None = None
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
    logging.getLogger("parishkit.stewardship").log(
        level,
        event,
        extra=log_extra({"correlation_id": _correlation.get(), "task_id": task_id}),
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
