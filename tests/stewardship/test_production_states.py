"""Production journal transitions never equate cleanup failure with gate release."""

from itertools import product

import pytest

from parishkit.stewardship.campaigns.production_states import (
    GATE_OWNING_PRODUCTION_STATES,
    production_target,
)
from parishkit.stewardship.campaigns.production_states import (
    ProductionAction as Action,
)
from parishkit.stewardship.campaigns.production_states import (
    ProductionState as State,
)

EXPECTED = {
    (State.QUEUED, Action.START): State.RUNNING,
    (State.RETRY_WAIT, Action.START): State.RUNNING,
    (State.RUNNING, Action.RECOVER): State.RUNNING,
    (State.RUNNING, Action.RETRY_LATER): State.RETRY_WAIT,
    (State.RUNNING, Action.FAIL): State.FAILED,
    (State.FAILED, Action.RETRY_FAILED): State.QUEUED,
    (State.RUNNING, Action.COMPLETE): State.COMPLETE,
    (State.COMPLETE, Action.ACTIVATE): State.ACTIVATED,
    (State.QUEUED, Action.CANCEL): State.CANCELLED,
    (State.RUNNING, Action.CANCEL): State.CANCELLED,
    (State.RETRY_WAIT, Action.CANCEL): State.CANCELLED,
    (State.COMPLETE, Action.CANCEL): State.CANCELLED,
    (State.FAILED, Action.CANCEL): State.CANCELLED,
}


@pytest.mark.parametrize("state,action", tuple(product(State, Action)))
def test_production_transition_matrix(state, action):
    """Final confirmation and explicit failed-work retry have distinct paths."""
    if (state, action) in EXPECTED:
        assert production_target(state, action) is EXPECTED[state, action]
    else:
        with pytest.raises(ValueError, match="transition is not permitted"):
            production_target(state, action)


def test_cleanup_failure_and_completion_keep_the_gate():
    """A paused, exhausted or completed cleanup is still not live activation."""
    assert {state.value for state in State} == {
        "cleanup_queued",
        "cleanup_running",
        "cleanup_retry_wait",
        "cleanup_complete",
        "cleanup_failed",
        "activated",
        "cancelled",
    }
    assert {
        State.QUEUED,
        State.RUNNING,
        State.RETRY_WAIT,
        State.COMPLETE,
        State.FAILED,
    } == GATE_OWNING_PRODUCTION_STATES
    assert all(state in GATE_OWNING_PRODUCTION_STATES for state, _ in EXPECTED)


@pytest.mark.parametrize(
    "state,action",
    [
        ("cleanup_queued", Action.START),
        (State.QUEUED, "start"),
        (None, Action.START),
        (State.QUEUED, None),
        ("PRIVATE-STATE", "PRIVATE-ACTION"),
        (True, False),
    ],
)
def test_production_requires_typed_commands_without_echoing_input(state, action):
    """External values cannot obtain transition permission by string equality."""
    with pytest.raises(TypeError) as error:
        production_target(state, action)
    assert str(error.value) == (
        "Production state and action must use the closed vocabulary."
    )
