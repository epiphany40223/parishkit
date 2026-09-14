"""Production-cleanup journal states; no mode changes or deletion authority.

The journal's owning workflow supplies readiness, original Admin intent, task
fences and safe batch boundaries. Cleanup completion alone never activates a
campaign. An exhausted request continues to own its gate until explicit retry
or cancellation; it cannot silently release Testing work.
"""

from enum import StrEnum


class ProductionState(StrEnum):
    """Request lifecycle, intentionally separate from campaign and task states."""

    QUEUED = "cleanup_queued"
    RUNNING = "cleanup_running"
    RETRY_WAIT = "cleanup_retry_wait"
    COMPLETE = "cleanup_complete"
    FAILED = "cleanup_failed"
    ACTIVATED = "activated"
    CANCELLED = "cancelled"


class ProductionAction(StrEnum):
    """Commands require fresh owning proof, not a caller-selected exemption."""

    START = "start"
    RECOVER = "recover"
    RETRY_LATER = "retry_later"
    FAIL = "fail"
    RETRY_FAILED = "retry_failed"
    COMPLETE = "complete"
    ACTIVATE = "activate"
    CANCEL = "cancel"


GATE_OWNING_PRODUCTION_STATES = frozenset(
    state
    for state in ProductionState
    if state not in {ProductionState.ACTIVATED, ProductionState.CANCELLED}
)

_TRANSITIONS = {
    ProductionAction.START: (
        frozenset({ProductionState.QUEUED, ProductionState.RETRY_WAIT}),
        ProductionState.RUNNING,
    ),
    ProductionAction.RECOVER: (
        frozenset({ProductionState.RUNNING}),
        ProductionState.RUNNING,
    ),
    ProductionAction.RETRY_LATER: (
        frozenset({ProductionState.RUNNING}),
        ProductionState.RETRY_WAIT,
    ),
    ProductionAction.FAIL: (
        frozenset({ProductionState.RUNNING}),
        ProductionState.FAILED,
    ),
    ProductionAction.RETRY_FAILED: (
        frozenset({ProductionState.FAILED}),
        ProductionState.QUEUED,
    ),
    ProductionAction.COMPLETE: (
        frozenset({ProductionState.RUNNING}),
        ProductionState.COMPLETE,
    ),
    ProductionAction.ACTIVATE: (
        frozenset({ProductionState.COMPLETE}),
        ProductionState.ACTIVATED,
    ),
    ProductionAction.CANCEL: (
        GATE_OWNING_PRODUCTION_STATES,
        ProductionState.CANCELLED,
    ),
}


def production_target(state, action):
    """Return only structural targets; the owning journal checks every guard."""
    if not isinstance(state, ProductionState) or not isinstance(
        action, ProductionAction
    ):
        raise TypeError("Production state and action must use the closed vocabulary.")
    sources, target = _TRANSITIONS[action]
    if state not in sources:
        raise ValueError("Production transition is not permitted.")
    return target
