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
    PROVIDER = "provider"
    EXCEPTION = "exception"
    ACTION = "action"


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
    PRIVILEGED_REAUTH = "privileged_reauthentication"
    DESTRUCTIVE_CONFIRMATION = "destructive_confirmation"
    INVALID_LINK = "family_link_invalid"


FIELDS = {
    ContextKind.REQUEST: {"method", "status", "outcome", "source_fingerprint"},
    ContextKind.TASK: {"task_id", "count", "version", "outcome"},
    ContextKind.EMAIL: {"message_id", "recipient_count", "outcome"},
    ContextKind.SOURCE: {"snapshot_id", "generation", "count", "outcome"},
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
    safe = {}
    for key, value in values.items():
        if key == "outcome":
            valid = isinstance(value, Outcome)
            safe[key] = value.value if valid else None
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
