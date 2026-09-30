"""Plain task, action and cleanup labels, and where Technical details may sit (#227).

The Admin pages show background task states, history actions and Testing
cleanup states in words through small include templates; the stored names
stay under Technical details. These tests keep every stored value labeled,
every task type named, and every Technical details disclosure out of a
region that live-status-v1.js replaces while it polls (an open disclosure
there would snap shut, and focus on it would be lost, at each update).
"""

import re
from html.parser import HTMLParser
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


# Elements without end tags never open a nesting level.
VOID = {"area", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}
INCLUDE = re.compile(r"{%\s*include\s+['\"]([^'\"]+)['\"][^%]*%}")


def expanded(name, depth=0):
    """A template's source with its includes inlined (a few levels deep)."""
    source = (TEMPLATES / name).read_text()
    if depth > 3:
        return source
    return INCLUDE.sub(lambda match: expanded(match.group(1), depth + 1), source)


class LiveRegionScan(HTMLParser):
    """Record Technical details disclosures that open inside a live region."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.live_depth = None
        self.found = []

    def handle_starttag(self, tag, attrs):
        """Track nesting; note a live region's start and any disclosure in it."""
        if tag in VOID:
            return
        names = dict(attrs)
        if self.live_depth is not None and "technical-details" in (
            names.get("class") or ""
        ):
            self.found.append(tag)
        self.stack.append(tag)
        if self.live_depth is None and "data-live-status" in names:
            self.live_depth = len(self.stack)

    def handle_endtag(self, tag):
        """Close the matching element, ending the live region with its own tag."""
        if tag in VOID or tag not in self.stack:
            return
        while self.stack:
            if self.live_depth is not None and len(self.stack) == self.live_depth:
                closing = self.stack[-1] == tag
            else:
                closing = False
            popped = self.stack.pop()
            if closing:
                self.live_depth = None
            if popped == tag:
                break


@pytest.mark.parametrize(
    "name",
    sorted(
        path.name
        for path in (TEMPLATES / "stewardship").glob("*.html")
        if "data-live-status" in path.read_text()
    ),
)
def test_technical_details_never_sit_inside_a_polled_region(name):
    """Polling replaces a live region's content; its disclosures must be outside."""
    scan = LiveRegionScan()
    scan.feed(expanded(f"stewardship/{name}"))
    assert scan.found == [], f"{name}: Technical details inside a live region"


def test_the_live_region_scan_catches_a_disclosure_inside():
    """The scan flags a disclosure in a region and passes one after it."""
    inside = LiveRegionScan()
    inside.feed(
        '<section data-live-status="x"><p>a<br></p>'
        '<details class="technical-details"></details></section>'
    )
    outside = LiveRegionScan()
    outside.feed(
        '<section data-live-status="x"><p>a</p></section>'
        '<details class="technical-details"></details>'
    )
    assert inside.found == ["details"] and outside.found == []
