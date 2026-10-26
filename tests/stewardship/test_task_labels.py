"""Plain task, action and cleanup labels (#227).

The Admin pages show background task states, history actions and Testing
cleanup states in words through small include templates; the stored names
are in the logs and pk-admin, never on a page (#2199). These tests keep
every stored value labeled and every task type named.
"""

import re
from pathlib import Path

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.campaigns.production_states import ProductionState
from parishkit.stewardship.jobs.models import TASK_ACTIONS, TASK_STATES
from parishkit.stewardship.jobs.views import TASK_NAMES

SOURCE = Path(__file__).parents[2] / "src/parishkit/stewardship"
TEMPLATES = SOURCE / "accounts/templates"


def label(template, name, value):
    """Render one label include for one stored value."""
    return render_to_string(f"stewardship/{template}", {name: value}).strip()


@pytest.mark.parametrize("state", TASK_STATES)
def test_every_task_state_has_words(state):
    """No stored task state is shown as its internal name."""
    text = label("task-state.html", "state", state)
    assert text and text != state and "_" not in text


@pytest.mark.parametrize("action", TASK_ACTIONS)
def test_every_task_history_action_has_words(action):
    """No stored history action is shown as its internal name."""
    text = label("task-action.html", "action", action)
    assert text and text != action and "_" not in text


@pytest.mark.parametrize("state", [state.value for state in ProductionState])
def test_every_cleanup_state_has_words(state):
    """No stored Testing cleanup state is shown as its internal name."""
    text = label("cleanup-state.html", "state", state)
    assert text and text != state and "_" not in text


@pytest.mark.parametrize(
    ("template", "name"),
    [
        ("task-state.html", "state"),
        ("task-action.html", "action"),
        ("cleanup-state.html", "state"),
    ],
)
def test_an_unknown_value_falls_back_to_its_stored_name(template, name):
    """A value added later still shows something rather than a blank."""
    assert "some_new_value" in label(template, name, "some_new_value").lower()


# Task types are declared as TASK_TYPE-style constants or literal task_type=
# arguments; together these name every type the code can create.
TASK_TYPE_DECLARATION = re.compile(
    r'\b[A-Z_]*TASK_TYPE\s*=\s*"([a-z_]+)"|\btask_type\s*=\s*"([a-z_]+)"'
)


def declared_task_types():
    """Every task type string declared in the stewardship package."""
    found = set()
    for path in SOURCE.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        for constant, literal in TASK_TYPE_DECLARATION.findall(path.read_text()):
            found.add(constant or literal)
    return found


def test_every_task_type_has_a_plain_name():
    """A new task type must not show its internal name to Admins."""
    declared = declared_task_types()
    assert len(declared) > 20
    assert sorted(declared - set(TASK_NAMES)) == []
