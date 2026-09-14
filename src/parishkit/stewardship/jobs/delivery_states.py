"""Closed outbox vocabulary, separate from TaskRun and schedule outcomes.

This table describes structural transitions only. The journal service must also
verify current authority, exact worker/attempt ownership, holds, deadlines and
provider evidence. In particular, naming a reconciliation or resend action is
not evidence that an uncertain delivery may be retried.
"""

from enum import StrEnum


class DeliveryState(StrEnum):
    """Only the three final outcomes permit sealed-substitution destruction."""

    PENDING = "pending"
    SUBMITTING = "submitting"
    RETRY_WAIT = "retry_wait"
    UNKNOWN = "delivery_unknown"
    DELIVERED = "delivered"
    FAILED = "permanent_failure"
    CANCELLED = "cancelled"


class DeliveryAction(StrEnum):
    """Distinct evidence-bearing commands prevent timeout from implying failure."""

    SUBMIT = "submit"
    ACCEPT = "accept"
    RETRY_UNACCEPTED = "retry_unaccepted"
    FAIL_UNACCEPTED = "fail_unaccepted"
    MARK_UNKNOWN = "mark_unknown"
    CANCEL_UNSENT = "cancel_unsent"
    RETRY_FAILED = "retry_failed"
    AUTHORIZE_RESEND = "authorize_resend"
    RETRY_IDEMPOTENT = "retry_idempotent"


TERMINAL_DELIVERY_STATES = frozenset(
    {DeliveryState.DELIVERED, DeliveryState.FAILED, DeliveryState.CANCELLED}
)

# The owning service distinguishes definitive non-acceptance from a contractual
# idempotent retry and from a human-authorized potentially duplicate resend.
# None can overwrite the uncertainty retained in an earlier numbered attempt.
_TRANSITIONS = {
    DeliveryAction.SUBMIT: (
        frozenset({DeliveryState.PENDING, DeliveryState.RETRY_WAIT}),
        DeliveryState.SUBMITTING,
    ),
    DeliveryAction.ACCEPT: (
        frozenset({DeliveryState.SUBMITTING, DeliveryState.UNKNOWN}),
        DeliveryState.DELIVERED,
    ),
    DeliveryAction.RETRY_UNACCEPTED: (
        frozenset({DeliveryState.SUBMITTING, DeliveryState.UNKNOWN}),
        DeliveryState.RETRY_WAIT,
    ),
    DeliveryAction.FAIL_UNACCEPTED: (
        frozenset({DeliveryState.SUBMITTING, DeliveryState.UNKNOWN}),
        DeliveryState.FAILED,
    ),
    DeliveryAction.MARK_UNKNOWN: (
        frozenset({DeliveryState.SUBMITTING}),
        DeliveryState.UNKNOWN,
    ),
    DeliveryAction.CANCEL_UNSENT: (
        frozenset({DeliveryState.PENDING, DeliveryState.RETRY_WAIT}),
        DeliveryState.CANCELLED,
    ),
    DeliveryAction.RETRY_FAILED: (
        frozenset({DeliveryState.FAILED}),
        DeliveryState.PENDING,
    ),
    DeliveryAction.AUTHORIZE_RESEND: (
        frozenset({DeliveryState.UNKNOWN}),
        DeliveryState.PENDING,
    ),
    DeliveryAction.RETRY_IDEMPOTENT: (
        frozenset({DeliveryState.SUBMITTING, DeliveryState.UNKNOWN}),
        DeliveryState.RETRY_WAIT,
    ),
}


def delivery_target(state, action):
    """Reject untyped or structurally impossible commands without exposing input.

    An idempotent command replay is handled by its immutable journal identity, not
    by adding same-state edges here. The journal must recheck replay admission.
    """
    if not isinstance(state, DeliveryState) or not isinstance(action, DeliveryAction):
        raise TypeError("Delivery state and action must use the closed vocabulary.")
    sources, target = _TRANSITIONS[action]
    if state not in sources:
        raise ValueError("Delivery transition is not permitted.")
    return target
