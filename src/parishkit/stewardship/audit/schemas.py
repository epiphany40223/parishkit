"""Closed operational contexts, with no arbitrary messages, URLs or value dumps.

Extend these schemas intentionally alongside a privacy regression test. Caller
supplied dictionaries are never treated as already sanitized, even when their
keys look harmless. Domain-specific census audit is owned by its submission
service; this shared operational context is deliberately not a PII container.
"""

import re
from enum import StrEnum
from uuid import UUID

from parishkit.stewardship.jobs.operational_content import IncidentKind
from parishkit.stewardship.observability import FailureKind


class ContextKind(StrEnum):
    REQUEST = "request"
    TASK = "task"
    EMAIL = "email"
    SOURCE = "source"
    MEMBER_SOURCE = "member_source"
    PROVIDER = "provider"
    EXCEPTION = "exception"
    ACTION = "action"
    BOUNDARY = "boundary"
    SCHEDULE = "schedule"
    TIMEOUT = "timeout"
    DUE_WORK = "due_work"
    FAILURE = "failure"
    RECOVERY = "recovery"


class Outcome(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    FAILED = "failed"
    RETRY = "retry"
    CANCELLED = "cancelled"
    CHANGED = "changed"


class ActorKind(StrEnum):
    PORTAL_USER = "portal_user"
    FAMILY = "family"
    SYSTEM = "system"
    OPERATOR = "operator"


class Action(StrEnum):
    CONFIGURATION_REQUEST = "configuration_requested"
    ROLES_APPLIED = "roles_applied"
    SECRET_REPLACEMENT = "secret_replacement_requested"
    # No page records this since the Family codes page was removed (#873);
    # it stays so past System log rows still validate and read.
    FAMILY_CODES_VIEWED = "family_codes_viewed"
    FAMILY_DIRECTORY_VIEWED = "family_directory_viewed"
    POSTAL_OUTREACH_VIEWED = "postal_outreach_viewed"
    MINISTRY_REPORT_VIEWED = "ministry_report_viewed"
    PRIVILEGED_REAUTH = "privileged_reauthentication"
    DESTRUCTIVE_CONFIRMATION = "destructive_confirmation"
    INVALID_LINK = "family_link_invalid"
    FAMILY_MAC_BACKFILLED = "family_mac_backfilled"
    SOURCE_COMPACTED = "source_compacted"
    FACTS_COMPACTED = "facts_compacted"
    FACTS_VERIFIED = "facts_verified"
    EXPORT_REQUESTED = "export_requested"
    EXPORT_CANCELLED = "export_cancelled"
    EXPORT_DOWNLOADED = "export_downloaded"
    SOURCE_PROMOTED = "source_promoted"
    CHAIR_RECONCILED = "chair_reconciled"
    SOURCE_REJECTED = "source_rejected"
    SOURCE_SUPERSEDED = "source_superseded"
    SOURCE_FALLBACK = "source_fallback_requested"
    BACKGROUND_VIEWED = "background_viewed"
    DELIVERY_VIEWED = "delivery_viewed"
    DAILY_DIGEST_VIEWED = "daily_digest_viewed"
    WEEKLY_DIGEST_VIEWED = "weekly_digest_viewed"
    PARTICIPATION_VIEWED = "participation_viewed"
    INFORMATION_VIEWED = "information_viewed"
    INFORMATION_UPDATED = "information_updated"
    MINISTRY_FOLLOWUP_VIEWED = "ministry_followup_viewed"
    MINISTRY_REQUEST_UPDATED = "ministry_request_updated"
    FINANCIAL_REPORT_VIEWED = "financial_report_viewed"
    TALENTS_REPORT_VIEWED = "talents_report_viewed"
    TALENTS_REPORT_EXPORTED = "talents_report_exported"
    RESPONSE_DASHBOARD_VIEWED = "response_dashboard_viewed"
    # The lists of Families behind the response funnel (#477): one event type
    # per list, so the audit names the list without a new context field.
    RESPONSE_SUBMITTED_LIST_VIEWED = "response_submitted_list_viewed"
    RESPONSE_SUBMITTED_LIST_EXPORTED = "response_submitted_list_exported"
    RESPONSE_STARTED_LIST_VIEWED = "response_started_list_viewed"
    RESPONSE_STARTED_LIST_EXPORTED = "response_started_list_exported"
    RESPONSE_NOT_OPENED_LIST_VIEWED = "response_not_opened_list_viewed"
    RESPONSE_NOT_OPENED_LIST_EXPORTED = "response_not_opened_list_exported"
    RESPONSE_REPEAT_LIST_VIEWED = "response_repeat_list_viewed"
    RESPONSE_REPEAT_LIST_EXPORTED = "response_repeat_list_exported"
    RESPONSE_DATA_QUALITY_LIST_VIEWED = "response_data_quality_list_viewed"
    RESPONSE_DATA_QUALITY_LIST_EXPORTED = "response_data_quality_list_exported"
    # One Family's timeline (#477): the subject is the Family's opaque record id.
    FAMILY_TIMELINE_VIEWED = "family_timeline_viewed"
    DASHBOARD_VIEWED = "dashboard_viewed"
    SYSTEM_LOGS_VIEWED = "system_logs_viewed"
    SYSTEM_LOGS_EXPORTED = "system_logs_exported"
    # Opening System health (ADM-13); its count is the problems it showed.
    SYSTEM_HEALTH_VIEWED = "system_health_viewed"
    PRESENCE_VIEWED = "family_presence_viewed"
    # The operator's one-time engagement backfill (#477), with its counts.
    FAMILY_ENGAGEMENT_BACKFILLED = "family_engagement_backfilled"
    USERS_VIEWED = "portal_users_viewed"
    SECURITY_EVENT_ACKNOWLEDGED = "security_event_acknowledged"
    CRITICAL_EVENTS_ACKNOWLEDGED = "critical_events_acknowledged"
    CREDENTIAL_RESULT_DISMISSED = "credential_result_dismissed"
    FAMILY_MAINTENANCE_STARTED = "family_maintenance_started"
    FAMILY_MAINTENANCE_ENDED = "family_maintenance_ended"
    HOSTED_FILE_UPLOADED = "hosted_file_uploaded"
    HOSTED_FILE_SLUG_CHANGED = "hosted_file_slug_changed"
    HOSTED_FILE_DELETED = "hosted_file_deleted"
    CHAIR_REVIEW_DECIDED = "chair_review_decided"
    CAMPAIGN_MINISTRIES_REQUESTED = "campaign_ministries_requested"
    SETUP_STARTED = "setup_started"
    SETUP_SOURCE_STARTED = "setup_source_started"
    SETUP_SOURCE_COMPLETED = "setup_source_completed"
    SETUP_FROZEN = "setup_frozen"
    SETUP_EXPIRED = "setup_expired"
    SETUP_CREDENTIAL_STAGED = "setup_credential_staged"
    SETUP_CREDENTIAL_SCRUBBED = "setup_credential_scrubbed"
    # Admin automation sessions (ADM-11): approval, ending (the reason is on
    # the session row), refused use, and an Administrator acknowledging the
    # automation notices on their own dashboard.
    AUTOMATION_SESSION_APPROVED = "automation_session_approved"
    AUTOMATION_SESSION_ENDED = "automation_session_ended"
    AUTOMATION_SESSION_REFUSED = "automation_session_refused"
    AUTOMATION_NOTICES_ACKNOWLEDGED = "automation_notices_acknowledged"
    # An automation session stood in for a recent Google sign-in on a
    # fresh-gated action (ADM-11 PR 5), in that action's transaction.
    AUTOMATION_FRESH_GATE = "automation_fresh_gate"
    # Admin automation commands that change state (ADM-11): one event per
    # change, admin_cmd_<area>_<verb>, whose subject is the automation
    # session (see the specification's "Audit attribution").
    ADMIN_CMD_SCHEDULE_CONFIRM = "admin_cmd_schedule_confirm"
    ADMIN_CMD_TASK_RETRY = "admin_cmd_task_retry"
    ADMIN_CMD_REFRESH_START = "admin_cmd_refresh_start"
    ADMIN_CMD_TEST_SAMPLE = "admin_cmd_test_sample"
    ADMIN_CMD_TEST_FAMILIES = "admin_cmd_test_families"
    ADMIN_CMD_DELIVERY_RESOLVE = "admin_cmd_delivery_resolve"
    ADMIN_CMD_EXPORT_CREATE = "admin_cmd_export_create"
    ADMIN_CMD_EXPORT_CANCEL = "admin_cmd_export_cancel"
    ADMIN_CMD_EXPORT_RETRY = "admin_cmd_export_retry"
    ADMIN_CMD_EXPORT_REGENERATE = "admin_cmd_export_regenerate"
    ADMIN_CMD_DIGEST_WEEKLY_REQUEST = "admin_cmd_digest_weekly_request"
    ADMIN_CMD_EXPORT_FINANCIAL = "admin_cmd_export_financial"
    ADMIN_CMD_EXPORT_INFORMATION = "admin_cmd_export_information"
    ADMIN_CMD_EXPORT_MINISTRY = "admin_cmd_export_ministry"
    ADMIN_CMD_EXPORT_MINISTRY_PACKET = "admin_cmd_export_ministry_packet"
    ADMIN_CMD_EXPORT_DIRECTORY = "admin_cmd_export_directory"
    ADMIN_CMD_EXPORT_POSTAL = "admin_cmd_export_postal"
    # test families-preview --names: the review's names as an export (#817).
    ADMIN_CMD_EXPORT_FAMILY_TEST_NAMES = "admin_cmd_export_family_test_names"
    ADMIN_CMD_DELIVERY_RESEND = "admin_cmd_delivery_resend"
    ADMIN_CMD_DELIVERY_REFUSAL_CLEAR = "admin_cmd_delivery_refusal_clear"


# Closed field identifiers are operational metadata, never census values.
MEMBER_SOURCE_FIELDS = frozenset(
    {
        "prefix",
        "first_name",
        "middle_name",
        "last_name",
        "suffix",
        "nickname",
        "maiden_name",
        "birth_date",
        "gender",
        "email",
        "home_phone",
        "mobile_phone",
        "work_phone",
        "death_date",
        "marital_status",
        "language",
    }
)

FIELDS = {
    ContextKind.SCHEDULE: {
        "definition_id",
        "previous_revision_id",
        "selected_revision_id",
        "cancelled_messages",
        "skipped_occurrences",
        "failed_occurrences",
        "delivered_slots",
    },
    ContextKind.BOUNDARY: {
        "occurrence_id",
        "kind",
        "intended_unix_microseconds",
        "actual_unix_microseconds",
        "lag_microseconds",
        "before_state",
        "after_state",
    },
    ContextKind.REQUEST: {"method", "status", "outcome", "source_fingerprint"},
    ContextKind.TASK: {"task_id", "count", "version", "outcome"},
    # Work stopped by a time limit (#293). ``what`` names the limit that
    # stopped it; the seconds are whole numbers; ``count`` is how many times
    # it happened when one entry summarizes several.
    ContextKind.TIMEOUT: {
        "task_id",
        "task_type",
        "attempt",
        "limit_seconds",
        "elapsed_seconds",
        "what",
        "helper",
        "count",
        "outcome",
    },
    # What made scheduled work late (#634); mirrored in
    # stewardship_safe_context_v1. Either the task type with how many tasks
    # were late and the worst lateness against ``limit_seconds``, or the
    # Family send (its schedule ``definition_id`` and ``revision_id``) that
    # stalled or overran, with its counts, how long since its last progress
    # and how long since it fell due (durations, not times), plus how many
    # other tasks broke the per-task rule in the same check.
    ContextKind.DUE_WORK: {
        "task_type",
        "count",
        "lag_seconds",
        "limit_seconds",
        "definition_id",
        "revision_id",
        "remaining_count",
        "done_count",
        "stall_seconds",
        "elapsed_seconds",
        "other_late_count",
        # A campaign start or close that ran late (#633): its occurrence and
        # boundary task, with ``lag_seconds`` against ``limit_seconds``.
        "occurrence_id",
        "task_id",
    },
    # What failed and what happens next (#633); mirrored in
    # stewardship_safe_context_v1. ``failure`` is a closed word naming what
    # failed (``FAILURES``) and ``failure_kind`` the exception's category
    # (``observability.FailureKind``); ``reason`` a closed provider result
    # (``REASONS``) and ``status`` an HTTP status. ``outcome`` says whether it
    # will be retried (``retry``, after ``retry_seconds``, as ``attempt`` of
    # ``attempt_limit``) or gave up (``failed``). Ids name the task, message or
    # snapshot involved; ``count`` how many failed together. ``command`` is an
    # Admin command-line command's catalog name (#617).
    ContextKind.FAILURE: {
        "failure",
        "failure_kind",
        "task_id",
        "task_type",
        "message_id",
        "version",
        "attempt",
        "attempt_limit",
        "retry_seconds",
        "status",
        "reason",
        "count",
        "outcome",
        "command",
    },
    # An operational incident that ended (#633), written by the database when
    # it resolves; mirrored in stewardship_safe_context_v1. ``log_id`` is the
    # entry that opened it (the recovery entry shares its correlation), with
    # how long the incident lasted and how many times it was observed.
    ContextKind.RECOVERY: {
        "incident_id",
        "incident_kind",
        "log_id",
        "elapsed_seconds",
        "count",
    },
    ContextKind.EMAIL: {"message_id", "recipient_count", "outcome", "reason"},
    ContextKind.SOURCE: {"snapshot_id", "generation", "count", "outcome"},
    ContextKind.MEMBER_SOURCE: {"family_duid", "member_duid", "field"},
    ContextKind.PROVIDER: {"status", "provider_fingerprint", "outcome"},
    ContextKind.EXCEPTION: {"outcome", "retryable"},
    ContextKind.ACTION: {
        "version",
        "before_version",
        "after_version",
        "outcome",
        "source_fingerprint",
        "candidate_fingerprint",
        "count",
        "matching_count",
        "page",
        "directory_reason",
        "directory_phone",
        "directory_response",
        "directory_sort",
        "directory_reach",
        "search_used",
        "exact_code_used",
        "ministry_duid",
        "ministry_duids",
        "ministry_operational",
        # A live campaign's Ministry selection change (#342): the selections
        # before it and the DUIDs it adds and removes; `ministry_duids` holds
        # the selections after it.
        "previous_ministry_duids",
        "added_ministry_duids",
        "removed_ministry_duids",
        # An Administrator's decision on a suspended Chairperson seed and the
        # reason entered for it: the decision is a closed word; the reason is
        # the Administrator's own bounded text, refused when it carries an
        # address-like token, since this context is not a container for
        # personal data.
        "decision",
        "review_reason",
        # A hosted file (#346): its placeholder name (a restricted charset,
        # never free text), stored type, size and digest. The original file
        # name is free text and is never recorded here.
        "file_slug",
        "previous_file_slug",
        "file_kind",
        "file_size",
        "file_fingerprint",
        # An on-request report page or download (#556): which system mode it
        # showed, its closed filter choice, the parish's talent option when
        # that is the filter, and the ParishSoft snapshot its names came
        # from. Never the search text, which can name a Family; whether a
        # search was used is the existing ``search_used``.
        "report_mode",
        "report_filter",
        "talent_option_id",
        "snapshot_id",
        # A response list's sort order (#851): its closed sort token.
        "report_sort",
    },
}

# Sorted, distinct Ministry DUID lists; mirrored in stewardship_safe_context_v1.
MINISTRY_LIST_FIELDS = frozenset(
    {
        "ministry_duids",
        "previous_ministry_duids",
        "added_ministry_duids",
        "removed_ministry_duids",
    }
)
REVIEW_DECISIONS = frozenset({"keep_role", "restore", "remove"})
# An on-request report's system mode and filter choices (#556); mirrored in
# stewardship_safe_context_v1. The filters are each report's own fixed
# choices: the response lists' ``show`` keys (reports.response_lists) and
# the Talents report's ``talent`` words, where ``option`` stands for a
# configured talent (recorded as ``talent_option_id``). A test keeps this
# set equal to the reports' choices.
REPORT_MODES = frozenset({"production", "testing"})
REPORT_FILTERS = frozenset(
    {
        "all",
        "invited",
        "uninvited",
        "progressed",
        "opened",
        "followed",
        "unfollowed",
        "mailing-name",
        "envelope",
        "any",
        "cannot_serve",
        "cannot_attend",
        "option",
    }
)
# A response list's sort order (#851); mirrored in stewardship_safe_context_v1.
# Each list sorts by any of its columns both ways (reports.response_lists): the
# column key ascending, ``-key`` descending. A test keeps this set equal to
# the lists' own tokens.
REPORT_SORT_COLUMNS = (
    "family",
    "duid",
    "envelope",
    "submitted",
    "submissions",
    "opened",
    "progressed",
    "invited",
    "link",
    "last",
    "mailing",
    "problem",
)
REPORT_SORTS = frozenset(
    token for key in REPORT_SORT_COLUMNS for token in (key, f"-{key}")
)
# Stored hosted-file types (#346); mirrored in stewardship_safe_context_v1.
HOSTED_FILE_KINDS = frozenset({"pdf", "docx", "xlsx", "pptx", "png", "jpeg"})

# What failed, for a ``failure`` context (#633); mirrored in
# stewardship_safe_context_v1, and each has a plain-language sentence in
# ``log_details``.
FAILURES = frozenset(
    {
        # A ParishSoft read (source.failures.classify_read_failure).
        "organization_mismatch",
        "destructive_change",
        "shifted_scan",
        "invalid_payload",
        "incomplete_collection",
        "invalid_response",
        "lease_unavailable",
        "configuration_activating",
        "configuration_busy",
        "scope_changed",
        "credential_unreadable",
        "credential_changed",
        "provider_status",
        "provider_timeout",
        "provider_unreachable",
        # The source health check refused the configured ParishSoft scope.
        "source_configuration",
        "organization_changed",
        # A health check the operational intake runs on every page.
        "source_health_check",
        "mail_health_check",
        "due_work_health_check",
        "backup_health_check",
        "web_health_check",
        # Web did not answer its liveness check for several minutes in a row
        # (#392 L1; jobs.web_health).
        "web_unresponsive",
        # Background tasks that give up visibly.
        "export_cleanup",
        "fact_verification",
        "source_retention",
        "family_engagement",
        # Administrator alerts and security notices (SQL triggers).
        "alert_mail",
        "security_mail",
        "slack_alert",
        # The mail provider as a whole (mail_health.sql).
        "smtp_systemic",
        "smtp_unavailable",
        # An Admin command-line command failed unexpectedly (#617;
        # admin_cli.record_failure): nothing changed, or, for a command that
        # may have committed a change, its outcome is unknown.
        "admin_command",
        "admin_command_outcome_unknown",
    }
)
# Closed provider results a ``reason`` may hold; mirrored in
# stewardship_safe_context_v1. ``no_deliverable_recipient`` is the email
# context's; the rest are outbox reasons a failed delivery records (#633).
REASONS = frozenset(
    {
        "no_deliverable_recipient",
        "smtp_transient",
        "smtp_unavailable",
        "smtp_permanent",
        "smtp_systemic",
        "smtp_delivery_unknown",
        "preparation_failed",
        "slack_not_sent",
        "slack_delivery_unknown",
    }
)
# Identifier-shaped words (a task type, an incident kind, a failure
# category): the shape SQL checks; Python also checks the closed set.
_WORD = re.compile(r"[a-z][a-z0-9_]{0,63}")

# The limits that can stop work (#293); mirrored in stewardship_safe_context_v1.
TIMEOUT_KINDS = frozenset(
    {
        "read_guard",
        "lease",
        "retention_budget",
        "drive_copy_budget",
        "drive_retry_budget",
        "drive_request",
        "drive_probe_wait",
        "statement_timeout",
        "lock_timeout",
        "transaction_timeout",
        "mail_helper",
        "source_helper",
        "provider_check",
        "renewal_drain",
        "control_lock",
        # A web worker the Gunicorn master killed because it was still
        # serving at the graceful-stop limit, or had stopped reporting
        # to the master past its timeout (#374; web_supervisor).
        "web_drain",
        "web_heartbeat",
        # Background work that stopped waiting for a configuration change
        # to finish activating (#429; activation_hold).
        "configuration_activation",
        # The scheduler's web liveness probe ran out of time (#392 L1;
        # jobs.web_health).
        "web_probe",
        # A full ParishSoft load stopped at its own time bound (#834;
        # source.failures.record_load_budget).
        "source_load_budget",
    }
)
# The helper processes a deadline can kill (#293), by their entry point;
# mirrored in stewardship_safe_context_v1.
TIMEOUT_HELPERS = frozenset(
    {
        "readiness_delivery_worker",
        "readiness_notification_worker",
        "family_delivery_worker",
        "digest_delivery_worker",
        "weekly_delivery_worker",
        "operational_mail_worker",
        "operational_slack_worker",
        "security_mail_worker",
        "provider_check_worker",
        "parishsoft_http_worker",
    }
)


def admin_commands():
    """The Admin command-line catalog's command names, a closed set (#617).

    Imported lazily: the catalog module logs through modules that import
    this one. SQL checks only the name's shape (lower-case words).
    """
    from parishkit.stewardship.admin_cli import BY_NAME

    return BY_NAME.keys()


def sanitize(kind, values):
    """Reject unknown fields/types rather than merely hiding secret-looking names."""
    if not isinstance(kind, ContextKind) or type(values) is not dict:
        raise ValueError("Context requires a canonical schema and mapping.")
    if values.keys() - FIELDS[kind]:
        raise ValueError("Context contains fields outside its approved schema.")
    if (
        kind in {ContextKind.MEMBER_SOURCE, ContextKind.BOUNDARY}
        and values.keys() != FIELDS[kind]
    ):
        raise ValueError("Structured diagnostics require complete identifiers.")
    safe = {}
    for key, value in values.items():
        if key == "outcome":
            valid = isinstance(value, Outcome)
            safe[key] = value.value if valid else None
        elif key == "reason":
            valid = type(value) is str and value in REASONS
            safe[key] = value
        elif key == "failure":
            valid = type(value) is str and value in FAILURES
            safe[key] = value
        elif key == "command":
            valid = type(value) is str and value in admin_commands()
            safe[key] = value
        elif key == "failure_kind":
            valid = isinstance(value, FailureKind)
            safe[key] = value.value if valid else None
        elif key == "incident_kind":
            valid = isinstance(value, IncidentKind)
            safe[key] = value.value if valid else None
        elif key == "kind":
            valid = type(value) is str and value in {"start", "close"}
            safe[key] = value
        elif key in {"before_state", "after_state"}:
            valid = type(value) is str and value in {
                "draft",
                "scheduled",
                "active",
                "closed",
                "archived",
                "purging",
                "purge_cleanup_failed",
                "purged",
            }
            safe[key] = value
        elif key == "field":
            valid = type(value) is str and value in MEMBER_SOURCE_FIELDS
            safe[key] = value
        elif key in {"family_duid", "member_duid", "ministry_duid"}:
            valid = type(value) is int and 0 < value < 2**31
            safe[key] = value
        elif key in MINISTRY_LIST_FIELDS:
            valid = (
                type(value) is list
                and all(type(item) is int and 0 < item < 2**31 for item in value)
                and value == sorted(set(value))
            )
            safe[key] = list(value) if valid else None
        elif key == "method":
            valid = type(value) is str and value in {"GET", "HEAD", "POST"}
            safe[key] = value
        elif key == "task_type":
            valid = type(value) is str and _WORD.fullmatch(value)
            safe[key] = value
        elif key == "what":
            valid = type(value) is str and value in TIMEOUT_KINDS
            safe[key] = value
        elif key == "helper":
            valid = type(value) is str and value in TIMEOUT_HELPERS
            safe[key] = value
        elif key == "decision":
            valid = type(value) is str and value in REVIEW_DECISIONS
            safe[key] = value
        elif key in {"file_slug", "previous_file_slug"}:
            valid = type(value) is str and re.fullmatch(
                r"[a-z0-9]+(-[a-z0-9]+)*", value
            )
            valid = valid and len(value) <= 64
            safe[key] = value
        elif key == "file_kind":
            valid = type(value) is str and value in HOSTED_FILE_KINDS
            safe[key] = value
        elif key == "report_mode":
            valid = type(value) is str and value in REPORT_MODES
            safe[key] = value
        elif key == "report_filter":
            valid = type(value) is str and value in REPORT_FILTERS
            safe[key] = value
        elif key == "report_sort":
            valid = type(value) is str and value in REPORT_SORTS
            safe[key] = value
        elif key == "review_reason":
            valid = type(value) is str and 0 < len(value) <= 500 and "@" not in value
            safe[key] = value if valid else None
        elif key.endswith("_id"):
            valid = isinstance(value, UUID)
            safe[key] = str(value) if valid else None
        elif key.endswith("_fingerprint"):
            valid = type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
            safe[key] = value
        elif key in {
            "retryable",
            "search_used",
            "exact_code_used",
            "ministry_operational",
        }:
            valid = type(value) is bool
            safe[key] = value
        elif key.startswith("directory_"):
            choices = {
                "directory_reason": {
                    "any",
                    "no_head",
                    "no_address",
                    "invalid_address",
                    "provider_refused",
                    "deliverable",
                },
                "directory_phone": {"any", "yes", "no"},
                "directory_response": {"any", "yes", "no"},
                "directory_sort": {"name", "name_desc", "duid"},
                # reports.directories.REACH plus "any" (#388 L1).
                "directory_reach": {"any", "email", "mail", "neither"},
            }
            valid = type(value) is str and value in choices[key]
            safe[key] = value
        else:
            valid = type(value) is int and 0 <= value <= 2**63 - 1
            if key == "status":
                valid = valid and 100 <= value <= 599
            safe[key] = value
        if not valid:
            raise ValueError("Context value does not match its approved type.")
    return safe
