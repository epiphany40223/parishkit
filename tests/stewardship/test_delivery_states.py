"""Exhaustive structural outbox transitions, independent of service admission."""

from itertools import product

import pytest

from parishkit.stewardship.jobs.delivery_states import (
    TERMINAL_DELIVERY_STATES,
    delivery_target,
)
from parishkit.stewardship.jobs.delivery_states import (
    DeliveryAction as Action,
)
from parishkit.stewardship.jobs.delivery_states import (
    DeliveryState as State,
)

# An independent expected table keeps every unlisted state/action pair denied.
EXPECTED = {
    (State.PENDING, Action.SUBMIT): State.SUBMITTING,
    (State.RETRY_WAIT, Action.SUBMIT): State.SUBMITTING,
    (State.SUBMITTING, Action.ACCEPT): State.DELIVERED,
    (State.UNKNOWN, Action.ACCEPT): State.DELIVERED,
    (State.SUBMITTING, Action.RETRY_UNACCEPTED): State.RETRY_WAIT,
    (State.UNKNOWN, Action.RETRY_UNACCEPTED): State.RETRY_WAIT,
    (State.SUBMITTING, Action.FAIL_UNACCEPTED): State.FAILED,
    (State.UNKNOWN, Action.FAIL_UNACCEPTED): State.FAILED,
    (State.SUBMITTING, Action.MARK_UNKNOWN): State.UNKNOWN,
    (State.PENDING, Action.CANCEL_UNSENT): State.CANCELLED,
    (State.RETRY_WAIT, Action.CANCEL_UNSENT): State.CANCELLED,
    (State.FAILED, Action.RETRY_FAILED): State.PENDING,
    (State.UNKNOWN, Action.AUTHORIZE_RESEND): State.PENDING,
    (State.SUBMITTING, Action.RETRY_IDEMPOTENT): State.RETRY_WAIT,
    (State.UNKNOWN, Action.RETRY_IDEMPOTENT): State.RETRY_WAIT,
}


@pytest.mark.parametrize("state,action", tuple(product(State, Action)))
def test_delivery_transition_matrix(state, action):
    """Listed edges resolve exactly and every other edge fails closed."""
    if (state, action) in EXPECTED:
        assert delivery_target(state, action) is EXPECTED[state, action]
    else:
        with pytest.raises(ValueError, match="transition is not permitted"):
            delivery_target(state, action)


def test_delivery_terminal_set_does_not_hide_uncertainty():
    """Only failed delivery has an explicit retry exception among terminal rows."""
    assert {state.value for state in State} == {
        "pending",
        "submitting",
        "retry_wait",
        "delivery_unknown",
        "delivered",
        "permanent_failure",
        "cancelled",
    }
    assert {
        State.DELIVERED,
        State.FAILED,
        State.CANCELLED,
    } == TERMINAL_DELIVERY_STATES
    terminal_edges = {
        pair: target
        for pair, target in EXPECTED.items()
        if pair[0] in TERMINAL_DELIVERY_STATES
    }
    assert terminal_edges == {(State.FAILED, Action.RETRY_FAILED): State.PENDING}


@pytest.mark.parametrize(
    "state,action",
    [
        ("pending", Action.SUBMIT),
        (State.PENDING, "submit"),
        (None, Action.SUBMIT),
        (State.PENDING, None),
        ("PRIVATE-STATE", "PRIVATE-ACTION"),
        (True, False),
    ],
)
def test_delivery_requires_typed_commands_without_echoing_input(state, action):
    """String coercion must not accidentally select a structural permission."""
    with pytest.raises(TypeError) as error:
        delivery_target(state, action)
    assert str(error.value) == (
        "Delivery state and action must use the closed vocabulary."
    )
