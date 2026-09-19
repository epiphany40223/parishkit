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
    POSTAL_OUTREACH_VIEWED = "postal_outreach_viewed"
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
    DASHBOARD_VIEWED = "dashboard_viewed"
    PRESENCE_VIEWED = "family_presence_viewed"
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
    },
}


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
                "purged",
            }
            safe[key] = value
        elif key == "field":
            valid = type(value) is str and value in MEMBER_SOURCE_FIELDS
            safe[key] = value
        elif key in {"family_duid", "member_duid"}:
            valid = type(value) is int and 0 < value < 2**31
            safe[key] = value
        elif key == "method":
            valid = type(value) is str and value in {"GET", "HEAD", "POST"}
            safe[key] = value
        elif key.endswith("_id"):
            valid = isinstance(value, UUID)
            safe[key] = str(value) if valid else None
        elif key.endswith("_fingerprint"):
            valid = type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
            safe[key] = value
        elif key == "retryable":
            valid = type(value) is bool
            safe[key] = value
        else:
            valid = type(value) is int and 0 <= value <= 2**63 - 1
            if key == "status":
                valid = valid and 100 <= value <= 599
            safe[key] = value
        if not valid:
            raise ValueError("Context value does not match its approved type.")
    return safe
