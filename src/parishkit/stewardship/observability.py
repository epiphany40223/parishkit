"""Redacted operational logging before campaign-specific audit schemas land."""

import copy
import logging
import os
import re
import sys
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import parse_qsl
from uuid import UUID, uuid4

from parishkit.logging import JsonLogFormatter, log_extra, setup_logging


class Event(StrEnum):
    """Reviewed event names; arbitrary user text is never an operational message."""

    CONFIG_REJECTED = "configuration_rejected"
    CONFIG_MISMATCH = "configuration_digest_mismatch"
    STARTUP_REJECTED = "startup_rejected"
    STARTUP_VALIDATED = "startup_validated"
    REQUEST_COMPLETED = "request_completed"
    REPORT_AUDIT_FAILED = "report_audit_failed"
    REPORT_SHAPING_FAILED = "report_shaping_failed"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    FACT_DRIFT = "fact_drift"
    DELIVERY_UNKNOWN = "delivery_unknown"
    BOUNDARY_LAG = "campaign_boundary_lag"
    DUE_WORK_LAG = "due_work_lag"
    PRODUCTION_CLEANUP_FAILED = "production_cleanup_failed"
    SOURCE_INVALID = "source_refresh_invalid"
    SOURCE_RETENTION_SKIPPED = "source_retention_skipped"
    SOURCE_TENANT_MISMATCH = "source_tenant_mismatch"
    SOURCE_DESTRUCTIVE_CHANGE = "source_destructive_change"
    SOURCE_MEMBER_UNUSABLE = "source_member_unusable"
    SOURCE_HELD = "source_refresh_held"
    SOURCE_CREDENTIAL_FAILED = "source_credential_failed"
    SOURCE_PROVIDER_FAILED = "source_provider_failed"
    MAIL_PROVIDER_FAILED = "mail_provider_failed"
    INSTALLER_REQUEST_FAILED = "installer_request_failed"
    HANDOFF_KEY_MISMATCH = "credential_handoff_key_mismatch"
    AUTHENTICATION_LIMITS_WEAKENED = "authentication_limits_weakened"
    AUTH_HEALTH_FAILED = "authentication_health_observation_failed"
    # Work stopped by a time limit (#293): what was stopped, after how long.
    TASK_TIMED_OUT = "task_timed_out"
    HELPER_TIMED_OUT = "helper_timed_out"
    WORK_BUDGET_REACHED = "work_budget_reached"
    TASK_LEASE_LOST = "task_lease_lost"
    UNSTRUCTURED = "unstructured_log_suppressed"


class FailureKind(StrEnum):
    """Safe operational categories, never exception text or credential values."""

    DATABASE = "database_unavailable"
    LIMITER = "authentication_limiter_unavailable"
    CREDENTIAL = "credential_unavailable"
    CONFIGURATION = "configuration_unavailable"
    FILESYSTEM = "filesystem_unavailable"
    UNEXPECTED = "unexpected_failure"
    # Operator refusals the runbooks name. The formatter drops free text, and
    # Event names are mirrored by a SQL constraint, so these ride on the
    # reviewed startup_rejected event as categories instead.
    BACKUP_DUMP = "backup_dump_failed"
    BACKUP_REQUIRED = "upgrade_backup_required"
    # A set was taken but not copied to the off-site Drive folder.
    BACKUP_OFFSITE = "backup_offsite_failed"
    # A backup sealed to a different public key than the previous run did.
    BACKUP_RECIPIENT_CHANGED = "backup_recipient_changed"
    # Backup retention skipped a run because the clock looked wrong: the
    # newest set is days after the one before it, or a set is dated after
    # the clock.
    BACKUP_RETENTION_GAP = "backup_retention_paused_gap"
    BACKUP_RETENTION_FUTURE = "backup_retention_paused_future_set"
    # A new integration key has been installed for a while but not selected,
    # so its consumers hold their work (#307 M1); it needs an Administrator.
    CREDENTIAL_SWITCH_UNFINISHED = "credential_switch_unfinished"


# The off-site copy's Drive failure categories, mirroring
# jobs.backup_models.FAILURE_KINDS, so a backup_offsite_failed line names why.
DRIVE_FAILURES = frozenset(
    {
        "authorization",
        "api_disabled",
        "not_found",
        "permission",
        "not_folder",
        "credential",
        "verification",
        "unavailable",
        "unexpected",
        "unanswered",
    }
)
# The time limits that can stop off-site backup work, named in the process
# log with the limit and the elapsed seconds (the timeout-logging rule):
# the whole copy's budget, a retry refused because it would pass that
# budget, one Drive request's own timeout, and a "Test access" check closed
# unanswered after waiting too long.
TIMEOUT_LIMITS = frozenset(
    {"drive_copy_budget", "drive_retry_budget", "drive_request", "drive_probe_wait"}
)


def _seconds(value):
    """Whether a value is whole, non-negative seconds (a bool is not)."""
    return type(value) is int and value >= 0


_correlation: ContextVar[UUID | None] = ContextVar(
    "stewardship_correlation", default=None
)


def current_correlation() -> UUID:
    """Reuse the bound request/task ID; create an ID for an unscoped operation."""
    return _correlation.get() or uuid4()


_task: ContextVar[UUID | None] = ContextVar("stewardship_task", default=None)


def current_task() -> UUID | None:
    """The background task this code runs for, when a worker bound one."""
    return _task.get()


@contextmanager
def task_scope(identifier: UUID):
    """Bind the running task so deep helpers (mail, provider checks) can name it."""
    if not isinstance(identifier, UUID):
        raise ValueError("task scope must be an internal UUID")
    token = _task.set(identifier)
    try:
        yield identifier
    finally:
        _task.reset(token)


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
    source_loss: tuple | None = None,
    source_max_drop_percent: int | None = None,
    drive_failure: str | None = None,
    timeout: str | None = None,
    limit_seconds: int | None = None,
    elapsed_seconds: int | None = None,
) -> None:
    """Emit only typed identifiers and an allowlisted event; accept no free text.

    ``source_loss`` is a refused source refresh's (closed measure name, count
    before, count after), only with ``SOURCE_DESTRUCTIVE_CHANGE``.
    ``source_max_drop_percent`` is a source refresh's overridden loss limit,
    only with ``TASK_STARTED``. Event names are mirrored by a SQL constraint,
    so the override rides on that reviewed event rather than a new one.

    ``drive_failure`` is an off-site copy's Drive category; ``timeout`` names
    the limit that stopped work, with that limit and the elapsed time in
    whole seconds. Each is a closed word or a number, never provider text.
    """
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
    if source_loss is not None and (
        event is not Event.SOURCE_DESTRUCTIVE_CHANGE or not _safe_loss(source_loss)
    ):
        raise ValueError("Source loss detail must be a reviewed measure and counts.")
    if source_max_drop_percent is not None and (
        event is not Event.TASK_STARTED or not _safe_percent(source_max_drop_percent)
    ):
        raise ValueError("A source loss limit must be a whole percent.")
    if drive_failure is not None and drive_failure not in DRIVE_FAILURES:
        raise ValueError("Drive failure categories must be reviewed values.")
    if timeout is not None and timeout not in TIMEOUT_LIMITS:
        raise ValueError("Timeout limits must be reviewed names.")
    if any(
        value is not None and not _seconds(value)
        for value in (limit_seconds, elapsed_seconds)
    ):
        raise ValueError("Timeout durations must be whole seconds.")
    logging.getLogger("parishkit.stewardship").log(
        level,
        event,
        extra=log_extra(
            {
                "correlation_id": _correlation.get(),
                "task_id": task_id,
                "authentication_limits": authentication_limits,
                "failure_kind": failure_kind,
                "source_loss": source_loss,
                "source_max_drop_percent": source_max_drop_percent,
                "drive_failure": drive_failure,
                "timeout": timeout,
                "limit_seconds": limit_seconds,
                "elapsed_seconds": elapsed_seconds,
            }
        ),
    )


def emit_failure(error, *, event=Event.TASK_FAILED):
    """Classify a failure without serializing any exception-controlled field."""
    from django.db import DatabaseError

    from parishkit.config import ConfigError

    from .accounts.credential_errors import CredentialValidationUnavailable
    from .accounts.cryptography import CryptographicError
    from .accounts.limiting import LimiterUnavailable

    kind = next(
        (
            kind
            for cls, kind in (
                (LimiterUnavailable, FailureKind.LIMITER),
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
    if debug_logging_enabled():
        # The reviewed event above carries only the category; say what failed.
        logging.getLogger("parishkit.stewardship.debug").debug(
            "failure detail for %s", event.value, exc_info=error
        )


@contextmanager
def installer_request(identifier):
    """Correlate a selected durable request's failures before unwinding its scope."""
    with correlation(identifier):
        try:
            yield
        except Exception as error:
            emit_failure(error, event=Event.INSTALLER_REQUEST_FAILED)
            raise


def _safe_percent(value):
    """A whole percent from 0 to 100."""
    return type(value) is int and 0 <= value <= 100


def _safe_loss(value):
    """A (measure, before, after) triple: a closed name and non-negative counts.

    ``before`` is ``None`` only for an empty load, which has no comparison.
    """
    from .source.loading import LOSS_MEASURES

    return (
        type(value) is tuple
        and len(value) == 3
        and type(value[0]) is str
        and value[0] in LOSS_MEASURES
        and all(item is None or (type(item) is int and item >= 0) for item in value[1:])
        and type(value[2]) is int
    )


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


# Pre-launch debugging switch. Deliberately not PARISHKIT_STEWARDSHIP_*: the
# deployment loader refuses unknown variables with that prefix.
DEBUG_LOGGING_VARIABLE = "PARISHKIT_DEBUG_LOGGING"


def debug_logging_enabled() -> bool:
    """Whether this process may log messages and tracebacks the formatter drops.

    Debug logs can carry personal data, provider responses and secrets from
    exception text, so this is for a pre-launch deployment holding disposable
    data only; it is off unless the variable is exactly "1".
    """
    return os.environ.get(DEBUG_LOGGING_VARIABLE) == "1"


def debug_swallowed(message: str) -> None:
    """Debug-log the exception being handled where a view hides its detail.

    Views that turn a failure into a closed, generic response (a denial or a
    "temporarily unavailable" page) otherwise leave no trace of what failed.
    Call this from inside the ``except`` block; outside one, or with debug
    logging off, it does nothing.
    """
    error = sys.exc_info()[1]
    if error is not None and debug_logging_enabled():
        logging.getLogger("parishkit.stewardship.debug").debug(message, exc_info=error)


def _debug_details(record: logging.LogRecord) -> dict:
    """The original logger, message and traceback, for debug logging only."""
    try:
        message = record.getMessage()
    except Exception:  # A broken format string must not lose the record.
        message = repr(record.msg)
    details = {"logger": record.name, "message": message}
    if record.exc_info:
        details["exception"] = "".join(traceback.format_exception(*record.exc_info))
    return details


# Secret-bearing URL parts (#311). A Family personal link carries a working
# sign-in token in its path ("/access/<token>"), and the Admin OAuth callback
# carries its one-time "code" and "state" in the query string. Debug logging
# keeps free text (Django's "Service Unavailable: /access/<token>" record, the
# runserver request line, exception messages), so the formatter scrubs every
# record here rather than trusting each log call to remember.
REDACTED = "[redacted]"
# Query parameters whose values are credentials, scrubbed wherever they appear.
SECRET_QUERY_NAMES = ("code", "state", "token")
# Literal scrubbing accepts only credential-shaped values: at least eight
# URL-safe characters. Shorter values are not credentials, and scrubbing them
# would only mangle the log.
_SECRET_SHAPE = re.compile(r"[\w\-.~%+/=]{8,}", re.ASCII)


def _encoded(text: str) -> str:
    """A pattern matching ``text`` with any character optionally %-encoded.

    Raw request URIs (gunicorn's "Error handling request", the runserver
    request line) are logged undecoded, and Django routes ``/access%2F<t>``
    and ``/%61ccess/<t>`` to the same personal-link view.
    """
    return "".join(f"(?:{re.escape(char)}|%{ord(char):02x})" for char in text)


# Values stop at quotes and backslashes so a match never spans a JSON escape,
# which keeps a redacted JSONL line valid.
_ACCESS_TOKEN = re.compile(rf"({_encoded('/access/')})[^/?#&\s\"'\\]+", re.IGNORECASE)
_QUERY_VALUE = re.compile(r"([?&][^=?&#\s\"'\\]+=)[^&#\s\"'\\]+")
_REQUEST_SECRETS: ContextVar[tuple[str, ...]] = ContextVar(
    "stewardship_request_secrets", default=()
)


def request_secrets(request) -> tuple[str, ...]:
    """The literal secret values one request carries, longest first.

    These are the personal-link token from an ``/access/`` path and the values
    of ``SECRET_QUERY_NAMES``. Scrubbing them literally also catches a token
    that reaches a log outside URL form, such as inside an exception message.
    Anything that is not a Django request yields nothing.
    """
    path = getattr(request, "path_info", None)
    values = []
    if isinstance(path, str) and path.startswith("/access/"):
        values.append(path.removeprefix("/access/").split("/")[0])
    # Parse the raw query string rather than touching request.GET, whose
    # field-count limit could raise here, ahead of the view's own handling.
    meta = getattr(request, "META", None)
    query = meta.get("QUERY_STRING", "") if isinstance(meta, dict) else ""
    values.extend(
        value for name, value in parse_qsl(query) if name in SECRET_QUERY_NAMES
    )
    return tuple(
        sorted(
            {value for value in values if _SECRET_SHAPE.fullmatch(value)},
            key=len,
            reverse=True,
        )
    )


def redact_secrets(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Remove secret path segments and query values from free log text.

    Replaces each literal ``secrets`` value, the segment after ``/access/``
    (also when %-encoded) and every query-string value, keeping the route and
    parameter names so the log still shows what was hit (``/access/[redacted]``,
    ``/admin/oauth/callback?code=[redacted]&state=[redacted]``). All query
    values are redacted, not just known names, so a future secret-bearing
    parameter cannot leak by default.
    """
    # Literal values are attacker-chosen (any query value of the right shape),
    # so only ever apply them to raw text, never to a serialized JSON line:
    # there they could split an escape or rename a field.
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    text = _ACCESS_TOKEN.sub(r"\g<1>" + REDACTED, text)
    return _QUERY_VALUE.sub(r"\g<1>" + REDACTED, text)


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
        if isinstance(context, dict):
            # Closed words and whole seconds only, re-checked here like the
            # other fields so a hand-built record cannot smuggle text through.
            if context.get("drive_failure") in DRIVE_FAILURES:
                safe.extra["drive_failure"] = context["drive_failure"]
            if context.get("timeout") in TIMEOUT_LIMITS:
                safe.extra["timeout"] = context["timeout"]
            for key in ("limit_seconds", "elapsed_seconds"):
                if _seconds(context.get(key)):
                    safe.extra[key] = context[key]
        if (
            record.msg is Event.AUTHENTICATION_LIMITS_WEAKENED
            and isinstance(context, dict)
            and _safe_thresholds(context.get("authentication_limits"))
        ):
            safe.extra["authentication_limits"] = list(context["authentication_limits"])
        if (
            record.msg is Event.SOURCE_DESTRUCTIVE_CHANGE
            and isinstance(context, dict)
            and _safe_loss(context.get("source_loss"))
        ):
            measure, before, after = context["source_loss"]
            safe.extra["source_loss"] = {
                "measure": measure,
                "before": before,
                "after": after,
            }
        if (
            record.msg is Event.TASK_STARTED
            and isinstance(context, dict)
            and _safe_percent(context.get("source_max_drop_percent"))
        ):
            safe.extra["source_max_drop_percent"] = context["source_max_drop_percent"]
        if debug_logging_enabled():
            # The debug details are the only free text in a line. Scrub each
            # value before serialization. The current request's secrets come
            # from the middleware's context; a record logged after the
            # middleware returned (django.request) carries its request instead.
            # Outside any request there are no literals, only the URL patterns.
            secrets = _REQUEST_SECRETS.get() + request_secrets(
                getattr(record, "request", None)
            )
            safe.extra["debug"] = {
                key: redact_secrets(value, secrets)
                for key, value in _debug_details(record).items()
            }
        # Defense in depth for any future free-text field: the URL patterns
        # alone stay inside one JSON string, so they cannot break the line.
        return redact_secrets(super().format(safe))


def configure_logging(config: dict | None = None) -> None:
    """Install redacted JSONL on stderr via shared logging; no provider handlers."""
    logger = setup_logging(verbose=True, debug=debug_logging_enabled())
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
        # Also expose this request's secrets to the formatter for the whole
        # request, so a token logged in any form is scrubbed (#311).
        secrets = _REQUEST_SECRETS.set(request_secrets(request))
        try:
            with correlation() as identifier:
                request.correlation_id = identifier
                response = self.get_response(request)
                response["X-Correlation-ID"] = str(identifier)
                return response
        finally:
            _REQUEST_SECRETS.reset(secrets)
