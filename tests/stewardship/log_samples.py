"""A complete synthetic context for every serious operational event (#633).

Tests that need a WARNING-or-above entry for some other purpose (the
critical-events banner, the operational intake, System logs) record one with
``operational(event, level=..., **sample(event))``, so each entry still meets
``audit.log_contract`` the way a real producer's does. The contract test
checks every sample against the contract and its schema.
"""

from uuid import UUID

from parishkit.stewardship.audit.schemas import ContextKind, Outcome
from parishkit.stewardship.observability import Event, FailureKind

TASK = UUID(int=0x633)
MESSAGE = UUID(int=0x634)
OCCURRENCE = UUID(int=0x635)

_FAILED = {"failure": "mail_health_check", "outcome": Outcome.FAILED}
_SOURCE_TASK = {
    "failure": "provider_status",
    "status": 503,
    "task_id": TASK,
    "attempt": 2,
    "attempt_limit": 5,
    "retry_seconds": 60,
    "outcome": Outcome.RETRY,
}

SAMPLES = {
    Event.TASK_FAILED: (ContextKind.FAILURE, _FAILED),
    Event.FACT_DRIFT: (
        ContextKind.TASK,
        {"task_id": TASK, "count": 2, "outcome": Outcome.FAILED},
    ),
    Event.FAMILY_ENGAGEMENT_FAILED: (
        ContextKind.FAILURE,
        {
            "failure": "family_engagement",
            "failure_kind": FailureKind.DATABASE,
            "outcome": Outcome.FAILED,
        },
    ),
    Event.SOURCE_PROVIDER_FAILED: (ContextKind.FAILURE, _SOURCE_TASK),
    Event.SOURCE_CREDENTIAL_FAILED: (
        ContextKind.FAILURE,
        _SOURCE_TASK | {"failure": "credential_unreadable"},
    ),
    Event.SOURCE_HELD: (
        ContextKind.FAILURE,
        _SOURCE_TASK | {"failure": "lease_unavailable"},
    ),
    Event.SOURCE_INVALID: (
        ContextKind.FAILURE,
        {"failure": "invalid_payload", "outcome": Outcome.FAILED},
    ),
    Event.SOURCE_TENANT_MISMATCH: (
        ContextKind.FAILURE,
        {"failure": "organization_mismatch", "outcome": Outcome.FAILED},
    ),
    Event.SOURCE_DESTRUCTIVE_CHANGE: (
        ContextKind.FAILURE,
        {"failure": "destructive_change", "outcome": Outcome.FAILED},
    ),
    Event.SOURCE_RETENTION_SKIPPED: (
        ContextKind.FAILURE,
        {"failure": "source_retention", "task_id": TASK, "count": 1},
    ),
    Event.SOURCE_MEMBER_UNUSABLE: (
        ContextKind.MEMBER_SOURCE,
        {"family_duid": 1, "member_duid": 2, "field": "email"},
    ),
    Event.MAIL_PROVIDER_FAILED: (
        ContextKind.FAILURE,
        {"failure": "smtp_systemic", "task_id": TASK, "message_id": MESSAGE},
    ),
    Event.DELIVERY_UNKNOWN: (ContextKind.EMAIL, {"message_id": MESSAGE}),
    Event.DUE_WORK_LAG: (
        ContextKind.DUE_WORK,
        {
            "task_type": "outbox_delivery",
            "count": 3,
            "lag_seconds": 1020,
            "limit_seconds": 90,
        },
    ),
    Event.BOUNDARY_LAG: (
        ContextKind.DUE_WORK,
        {
            "task_type": "campaign_boundary",
            "task_id": TASK,
            "occurrence_id": OCCURRENCE,
            "lag_seconds": 120,
            "limit_seconds": 90,
        },
    ),
    Event.PRODUCTION_CLEANUP_FAILED: (ContextKind.TASK, {"task_id": TASK, "count": 4}),
    Event.WEB_UNHEALTHY: (
        ContextKind.FAILURE,
        {
            "failure": "web_unresponsive",
            "failure_kind": FailureKind.WEB_PROBE_TIMEOUT,
            "count": 3,
        },
    ),
    Event.ADMIN_COMMAND_FAILED: (
        ContextKind.FAILURE,
        {
            "failure": "admin_command",
            "failure_kind": FailureKind.UNEXPECTED,
            "command": "export create",
        },
    ),
    Event.CONFIG_MISMATCH: (ContextKind.EXCEPTION, {"outcome": Outcome.CHANGED}),
    Event.TASK_TIMED_OUT: (ContextKind.TIMEOUT, {"what": "lease"}),
    Event.HELPER_TIMED_OUT: (ContextKind.TIMEOUT, {"what": "mail_helper"}),
    Event.WORK_BUDGET_REACHED: (ContextKind.TIMEOUT, {"what": "retention_budget"}),
    Event.TASK_LEASE_LOST: (ContextKind.TIMEOUT, {"what": "lease"}),
}


def sample(event):
    """``schema=`` and ``context=`` arguments for a serious ``event`` entry."""
    schema, context = SAMPLES[event]
    return {"schema": schema, "context": dict(context)}
