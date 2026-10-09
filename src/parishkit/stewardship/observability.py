"""Redacted operational logging before campaign-specific audit schemas land."""

import copy
import functools
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
    # A ParishSoft Ministry name had to be repaired for display (#341); the
    # line carries the Ministry's DUID only, never the name itself.
    SOURCE_MINISTRY_NAME_REPAIRED = "source_ministry_name_repaired"
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
    # A Family sign-in or form issuance could not record its engagement row
    # (#477); the request itself went ahead and the funnel undercounts it.
    FAMILY_ENGAGEMENT_FAILED = "family_engagement_failed"
    # A process could not write its service status record (ADM-13); it goes
    # on working, and the System health page shows it as out of date.
    SERVICE_STATUS_FAILED = "service_status_failed"
    # A problem that had opened an operational incident has ended (#633):
    # written by the database when the incident resolves, linking back to the
    # entry that opened it. Durable log only.
    INCIDENT_RECOVERED = "incident_recovered"
    UNSTRUCTURED = "unstructured_log_suppressed"


class FailureKind(StrEnum):
    """Safe operational categories, never exception text or credential values."""

    DATABASE = "database_unavailable"
    # The database answered but a constraint or guard refused the statement
    # (see _GUARD_REFUSALS); not an outage. It may clear on retry (an expired
    # lease, an unreleased gate) or need a look at the data or the code.
    DATABASE_REFUSED = "database_write_refused"
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
    # A local set's sealed file no longer matches the SHA-256 its manifest
    # recorded, so it is not copied off-site.
    BACKUP_SET_MISMATCH = "backup_set_mismatch"
    # Backup retention skipped a run because the clock looked wrong: the
    # newest set is days after the one before it, or a set is dated after
    # the clock.
    BACKUP_RETENTION_GAP = "backup_retention_paused_gap"
    BACKUP_RETENTION_FUTURE = "backup_retention_paused_future_set"
    # A new integration key has been installed for a while but not selected,
    # so its consumers hold their work (#307 M1); it needs an Administrator.
    CREDENTIAL_SWITCH_UNFINISHED = "credential_switch_unfinished"
    # A refused Admin automation session use (ADM-11): an unknown secret, a
    # host mismatch or a command session's key presented to the web. The
    # detail is the automation notice and audit event, never the secret.
    AUTOMATION_REFUSED = "automation_session_refused"
    # No ParishSoft data was left to read Family head emails from (#604): the
    # directory's or an export's captured source was compacted and, for an
    # export, there is no current promoted source either.
    HEAD_EMAILS_UNAVAILABLE = "directory_head_emails_unavailable"
    # A scheduled ParishSoft full refresh falls inside the lead window in
    # which the bulk Family send prepares a Production reminder (BG-12,
    # #447): its promotion would pause that preparation until the Family
    # population is rebuilt. Warned once per scheduler process for each
    # campaign and configuration, riding on the reviewed startup_validated
    # event until it has its own (see the follow-up issue on #447).
    REFRESH_IN_LEAD_WINDOW = "full_refresh_in_lead_window"
    # The database's refresh-tick guard refused the schedule-change catch-up
    # full refresh (#632): only that request was rolled back, and the
    # scheduler's other refreshes still run. Logged once per scheduler
    # process for each catch-up slot, riding on startup_validated like the
    # lead-window advice; not a failed refresh.
    REFRESH_CATCH_UP_REFUSED = "refresh_catch_up_refused"
    # The database's slot decision guard refused a scheduler's skip or hold
    # record (#632): only that record was rolled back. Logged once per
    # scheduler process for each slot, like a refused catch-up.
    REFRESH_DECISION_REFUSED = "refresh_decision_refused"
    # The campaign's Reminder WorkGroup (#861) names no ParishSoft Family
    # WorkGroup, so no Family's Reminders are skipped. Warned on each refresh
    # that reads it, riding on the refresh's reviewed task_started event.
    REMINDER_WORKGROUP_MISSING = "reminder_workgroup_missing"
    # ``pk-stewardship load-check`` stopped because something it measures
    # changed under it (#633): the ParishSoft data, the Testing Family
    # portal, or the campaign. Each says to run the check again.
    LOAD_CHECK_SOURCE_CHANGED = "load_check_source_changed"
    # A WARNING-or-above operational entry lacked the context its event must
    # carry (audit.log_contract, #633); it was written anyway. Logged on the
    # entry's own event so the line names it.
    LOG_CONTRACT_INCOMPLETE = "log_contract_incomplete"
    LOAD_CHECK_PORTAL_CLOSED = "load_check_portal_closed"
    LOAD_CHECK_CAMPAIGN_UNAVAILABLE = "load_check_campaign_unavailable"


# The off-site copy's Drive failure categories, mirroring
# jobs.backup_models.FAILURE_KINDS, so a backup_offsite_failed line names why.
# Which display-only comparison a report_shaping_failed line came from, when
# one refresh can log it for more than one step. Event names are mirrored by
# a SQL constraint, so the step rides on the reviewed event instead.
SHAPING_STEPS = frozenset(
    {
        # A refresh's changed-record counts against its base (#242).
        "source_changes",
        # A full refresh's Ministry catalog differences (#342).
        "ministry_catalog",
    }
)
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
# The time limits named in the process log with the limit and the elapsed
# seconds (the timeout-logging rule). The off-site backup's own: the whole
# copy's budget, a retry refused because it would pass that budget, one
# Drive request's own timeout, and a "Test access" check closed unanswered
# after waiting too long. Every durable timeout entry's ``what``
# (audit.schemas.TIMEOUT_KINDS, pinned by a test), so its process-log line
# carries the same facts when the durable write fails. And
# ``timeout_log_slot``: a timeout entry that gave up waiting for its
# process's one timeout-log connection (audit.timeouts). And
# ``family_sweep_budget``: the scheduler's Family schedule sweep ended a page
# at its time budget (pacing only; it resumes on the next loop, #394). And
# ``configuration_activation``: work that stopped waiting for a configuration
# change to finish activating (activation_hold, #429). And
# ``startup_database_wait``: an online service that stopped waiting at startup
# for its database to accept a connection (runtime_database.await_database,
# #453); process log only, since the database is what is unavailable.
TIMEOUT_LIMITS = frozenset(
    {
        "drive_copy_budget",
        "drive_retry_budget",
        "drive_request",
        "drive_probe_wait",
        "read_guard",
        "lease",
        "retention_budget",
        "statement_timeout",
        "lock_timeout",
        "transaction_timeout",
        "mail_helper",
        "source_helper",
        "provider_check",
        "renewal_drain",
        "control_lock",
        "web_drain",
        "web_heartbeat",
        "timeout_log_slot",
        "family_sweep_budget",
        "configuration_activation",
        "startup_database_wait",
        # ``pk-stewardship admin login wait`` stopped waiting for the
        # Administrator to approve a pairing (ADM-11); process log only.
        "automation_pairing_wait",
        # A ``pk-stewardship admin`` ``--watch`` reached its ``--timeout``
        # before the read finished (ADM-11); process log only.
        "automation_watch",
        # ``pk-stewardship restore-check`` killed pg_restore while it read a
        # dump's migration records (#608); process log only.
        "restore_check_dump",
        # ``pk-stewardship restore-compare`` killed pg_restore while it
        # loaded a dump into its scratch database (#608); process log only.
        "restore_compare_load",
    }
)


def _seconds(value):
    """Whether a value is whole, non-negative seconds (a bool is not)."""
    return type(value) is int and value >= 0


# A failure line's ``error_class`` (#612): the ``module.qualname`` of the
# exception's type, which code controls, never its message. Only dotted
# identifiers pass (a class defined inside a function has a ``<locals>``
# segment), so nothing an exception's text or arguments hold can get through.
_CLASS_NAME = re.compile(r"[A-Za-z_]\w*(?:\.(?:[A-Za-z_]\w*|<locals>))*", re.ASCII)
CLASS_NAME_LIMIT = 200


def class_name_of(error):
    """The ``module.qualname`` of an exception's type, for an ``error_class``."""
    kind = type(error)
    return f"{kind.__module__}.{kind.__qualname__}"


def _class_name(value):
    """Whether a value is a bounded dotted class name, nothing else."""
    return (
        type(value) is str
        and len(value) <= CLASS_NAME_LIMIT
        and _CLASS_NAME.fullmatch(value) is not None
    )


_correlation: ContextVar[UUID | None] = ContextVar(
    "stewardship_correlation", default=None
)


def current_correlation() -> UUID:
    """Reuse the bound request/task ID; create an ID for an unscoped operation."""
    return _correlation.get() or uuid4()


def bound_correlation() -> UUID | None:
    """The bound request/task ID, or None outside any correlation scope."""
    return _correlation.get()


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
    ministry_duid: int | None = None,
    shaping: str | None = None,
    error_class: str | None = None,
    watched_command: str | None = None,
    subject_id: UUID | None = None,
    confirmation: str | None = None,
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
    ``ministry_duid`` is a source Ministry's positive integer DUID, only with
    ``SOURCE_MINISTRY_NAME_REPAIRED``. ``shaping`` names the display-only
    comparison (``SHAPING_STEPS``), only with ``REPORT_SHAPING_FAILED``.
    ``error_class`` names a failure's exception type (see ``class_name_of``),
    only with a ``failure_kind``. ``watched_command`` (a watchable Admin CLI
    command's catalog name) and ``subject_id`` (the UUID of the record it
    followed) say what an ``automation_watch`` timeout was waiting for
    (#807), only with that timeout. ``confirmation`` is how an Admin automation
    command was confirmed (``prompt`` or ``yes``), only with ``TASK_STARTED``.
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
    if ministry_duid is not None and (
        event is not Event.SOURCE_MINISTRY_NAME_REPAIRED or not _duid(ministry_duid)
    ):
        raise ValueError("A Ministry DUID must be a positive source identity.")
    if shaping is not None and (
        event is not Event.REPORT_SHAPING_FAILED or shaping not in SHAPING_STEPS
    ):
        raise ValueError("A shaping step must be a reviewed name.")
    if error_class is not None and (
        failure_kind is None or not _class_name(error_class)
    ):
        raise ValueError("An error class must be a failure's dotted class name.")
    if (watched_command is not None or subject_id is not None) and (
        timeout != "automation_watch"
    ):
        raise ValueError("A watched command belongs only to a watch's timeout.")
    if watched_command is not None and watched_command not in watch_commands():
        raise ValueError("A watched command must be a watchable catalog name.")
    if subject_id is not None and not isinstance(subject_id, UUID):
        raise ValueError("A watched record is named by its UUID.")
    if confirmation is not None and (
        event is not Event.TASK_STARTED or confirmation not in {"prompt", "yes"}
    ):
        raise ValueError("A confirmation is prompt or yes, as a command starts.")
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
                "ministry_duid": ministry_duid,
                "shaping": shaping,
                "error_class": error_class,
                "watched_command": watched_command,
                "subject_id": subject_id,
                "confirmation": confirmation,
            }
        ),
    )


@functools.cache
def watch_commands():
    """The Admin CLI commands ``--watch`` may repeat: a closed set of catalog
    names, read from the command catalog itself (imported here, lazily, since
    that module logs through this one) and cached, since the catalog is fixed
    at import."""
    from .admin_cli import COMMANDS

    return frozenset(spec.name for spec in COMMANDS if spec.watch)


# SQLSTATEs that mean the database answered and refused: an integrity
# constraint or a guard trigger's ERRCODE='23514' (class 23), a guard's
# ERRCODE='42501', or a guard's RAISE without an ERRCODE (P0001).
_GUARD_REFUSALS = ("23", "42501", "P0001")


def _guard_refusal(error):
    """Tell whether a database error is a refusal rather than an outage."""
    from django.db import DatabaseError

    state = getattr(error.__cause__, "sqlstate", None) or ""
    return isinstance(error, DatabaseError) and state.startswith(_GUARD_REFUSALS)


def failure_kind_of(error):
    """The closed ``FailureKind`` of an exception, never its text (#633).

    Shared by ``emit_failure`` and the durable entries that record a failure's
    category (audit ``failure_kind``), so both name a failure the same way.
    """
    from django.db import DatabaseError, IntegrityError

    from parishkit.config import ConfigError

    from .accounts.credential_errors import CredentialValidationUnavailable
    from .accounts.cryptography import CryptographicError
    from .accounts.limiting import LimiterUnavailable

    kind = FailureKind.DATABASE_REFUSED if _guard_refusal(error) else None
    # A reviewed class attribute (never instance data) can name its own kind.
    declared = getattr(type(error), "failure_kind", None)
    kind = kind or (declared if isinstance(declared, FailureKind) else None)
    kind = kind or next(
        (
            kind
            for cls, kind in (
                (LimiterUnavailable, FailureKind.LIMITER),
                # Before DatabaseError, its base class: first match wins.
                (IntegrityError, FailureKind.DATABASE_REFUSED),
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
    return kind


def emit_failure(
    error,
    *,
    event=Event.TASK_FAILED,
    level=logging.ERROR,
    task_id=None,
    shaping=None,
    name_class=False,
):
    """Classify a failure without serializing any exception-controlled field.

    ``level`` lowers the severity for a best-effort step whose failure the
    caller absorbs; ``task_id`` names the task it happened in; ``shaping``
    names which display-only comparison failed (see ``emit``). ``name_class``
    adds the exception type's ``error_class``, for a caller whose failure
    has no other trace (the admin command line, #612).
    """
    kind = failure_kind_of(error)
    # A name the allowlist refuses (non-ASCII, overlong, or a type() name
    # such as "bad-name") is left out rather than refused: emit would raise,
    # and the failure would lose its only line.
    name = class_name_of(error) if name_class else None
    emit(
        event,
        level=level,
        task_id=task_id,
        failure_kind=kind,
        shaping=shaping,
        error_class=name if _class_name(name) else None,
    )
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


def _duid(value):
    """Whether a value is a positive signed-32-bit source identity (not a bool)."""
    return type(value) is int and 0 < value < 2**31


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
            # Re-checked like the other fields: a dotted class name, and only
            # beside the failure category it explains.
            if _class_name(context.get("error_class")):
                safe.extra["error_class"] = context["error_class"]
        if isinstance(context, dict):
            # Closed words and whole seconds only, re-checked here like the
            # other fields so a hand-built record cannot smuggle text through.
            if context.get("drive_failure") in DRIVE_FAILURES:
                safe.extra["drive_failure"] = context["drive_failure"]
            if context.get("timeout") in TIMEOUT_LIMITS:
                safe.extra["timeout"] = context["timeout"]
                # What a watch was waiting for (#807): re-checked, and only
                # beside a watch's own timeout.
                if context["timeout"] == "automation_watch":
                    if context.get("watched_command") in watch_commands():
                        safe.extra["watched_command"] = context["watched_command"]
                    if isinstance(context.get("subject_id"), UUID):
                        safe.extra["subject_id"] = str(context["subject_id"])
            if record.msg is Event.TASK_STARTED and context.get("confirmation") in {
                "prompt",
                "yes",
            }:
                safe.extra["confirmation"] = context["confirmation"]
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
        if (
            record.msg is Event.SOURCE_MINISTRY_NAME_REPAIRED
            and isinstance(context, dict)
            and _duid(context.get("ministry_duid"))
        ):
            safe.extra["ministry_duid"] = context["ministry_duid"]
        if (
            record.msg is Event.REPORT_SHAPING_FAILED
            and isinstance(context, dict)
            and context.get("shaping") in SHAPING_STEPS
        ):
            safe.extra["shaping"] = context["shaping"]
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
