"""Closed operational contexts, with no arbitrary messages, URLs or value dumps.

Extend these schemas intentionally alongside a privacy regression test. Caller
supplied dictionaries are never treated as already sanitized, even when their
keys look harmless. Domain-specific census audit is owned by its submission
service; this shared operational context is deliberately not a PII container.
"""

import re
from enum import StrEnum
from uuid import UUID


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
    DASHBOARD_VIEWED = "dashboard_viewed"
    SYSTEM_LOGS_VIEWED = "system_logs_viewed"
    SYSTEM_LOGS_EXPORTED = "system_logs_exported"
    PRESENCE_VIEWED = "family_presence_viewed"
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
# Stored hosted-file types (#346); mirrored in stewardship_safe_context_v1.
HOSTED_FILE_KINDS = frozenset({"pdf", "docx", "xlsx", "pptx", "png", "jpeg"})

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
            valid = type(value) is str and value == "no_deliverable_recipient"
            safe[key] = value
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
            valid = type(value) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value)
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
