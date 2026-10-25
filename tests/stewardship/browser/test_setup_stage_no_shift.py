"""Setup's stage boxes hold their place as the work moves on (#1903).

Load parish data's status sentence above its boxes, and Finishing setup's
boxes themselves, change their words on every state. Each sits in one grid
cell over invisible copies of its other running forms (ui-conventions,
"Stage boxes"), so from the first rendering on, every box keeps its place
and size through each state the page shows while it still updates itself.
Each test answers the page's polls with one state after another and
measures every box after each swap.
"""

from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.domain import Percentage

from .conftest import (
    NOW,
    SETUP_FINISHING_STATUS,
    SETUP_LOAD_STATUS,
    finishing_context,
    load_collections,
)
from .setup_live_components import finishing_status, load_status
from .test_in_place import MARK, MARKED
from .test_setup_live_status import answer, credential, observation
from .waits import has_attribute, recorded, same_place

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Where each of ``selector``'s elements is drawn on the page (not in the
# viewport, which focus may scroll), with its size.
PLACES = (
    "nodes => nodes.map(node => { const box = node.getBoundingClientRect();"
    " return [box.left + scrollX, box.top + scrollY, box.width, box.height]; })"
)


def places(page, selector):
    """Every ``selector`` element's place and size, in document order."""
    return page.locator(selector).evaluate_all(PLACES)


def next_poll(page, polls, count):
    """Advance the page's clock until it has sent ``count`` polls."""
    for _ in range(30):
        if len(polls) >= count:
            break
        page.clock.fast_forward(2000)
        page.wait_for_timeout(50)
    recorded(page, polls, count)


def running(**values):
    """A source-load reading while the load still runs."""
    return observation(**({"collections": load_collections()} | values))


@pytest.mark.parametrize("width", [320, 760, 1280])
def test_load_parish_data_boxes_keep_their_place(page, component_origin, width):
    """The load's boxes, and the lines after them, never move as the status
    sentence above them changes, from waiting for the loading service to
    downloading (one line to three, #1903), saving (when the saved-record
    bar appears) and checking."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": width, "height": 900})
    page.clock.install(time=NOW)
    page.route(
        "**/admin/setup/source/*?format=json",
        lambda route: route.fulfill(json=running()),
    )
    # Each reading, with the words the rosters and funds rows must show
    # once it is drawn (None: the change of status sentence is enough).
    # The downloaded rows change their words as items finish, which must
    # not wrap a row, whatever the width.
    states = [
        ("queued", running(task_state="queued", phase="unspecified"), None),
        ("retry_wait", running(task_state="retry_wait", phase="unspecified"), None),
        ("staging", running(phase="staging", current=1234, total=5000), None),
        ("validating", running(phase="validating", current=5000, total=5000), None),
        (
            "fetching",
            running(collections=load_collections(2, finished=3, expected=40)),
            ("Waiting", "Waiting"),
        ),
        (
            "fetching",
            running(collections=load_collections(5, finished=40, expected=40)),
            ("40 of 40 loaded", "Waiting"),
        ),
        (
            "fetching",
            running(collections=load_collections(6, finished=40, expected=40)),
            ("40 of 40 loaded", "Downloading…"),
        ),
        ("fetching", running(), ("Waiting", "Waiting")),
    ]
    polls = answer(
        page, SETUP_LOAD_STATUS, [load_status(state, None) for _, state, _ in states]
    )
    page.goto(component_origin + "/setup-source-progress")
    page.evaluate(MARK)
    region = page.locator("[data-live-status=setup-load]")
    has_attribute(region, "data-progress-state", "fetching")
    boxes = places(page, "[data-load-phase]")
    assert len(boxes) == 4  # Start the load, then the load's three parts
    # The line after the saved-record bar, which keeps its space until
    # saving starts, so this line and its "i" button never move either.
    quiet = places(page, "#setup-load-quiet-label")
    rows = places(page, "[data-collection]")
    assert len(rows) == 7
    for count, (key, _state, words) in enumerate(states, start=1):
        next_poll(page, polls, count)
        has_attribute(region, "data-progress-state", key)
        if words:
            for row, text in zip(("ministry_roster", "funds"), words, strict=True):
                expect(
                    page.locator(
                        f"[data-collection={row}] > .setup-load-status-slot"
                    ).locator("[data-load-status]")
                ).to_have_text(text)
        assert same_place(places(page, "[data-load-phase]"), boxes), key
        # Every downloaded row keeps its place and height too, so none
        # wraps to another line as its words change.
        assert same_place(places(page, "[data-collection]"), rows), (key, count)
        assert same_place(places(page, "#setup-load-quiet-label"), quiet), key
        # Every downloaded row keeps its place and height too, so none
        # wraps to another line as its words change.
        assert same_place(places(page, "[data-collection]"), rows), (key, count)
    assert page.evaluate(MARKED) == "kept"


@pytest.mark.parametrize("width", [320, 760, 1280])
def test_finishing_setup_boxes_keep_their_place(page, component_origin, width):
    """Every Finishing setup box keeps its place and size as its words change,
    from waiting through installing, the server operator's acknowledgement,
    preparing the configuration, the final load waiting for a worker and
    the worker picking it up (#1903, #1916); the running final load shows
    no task reference (#1969)."""
    page.set_viewport_size({"width": width, "height": 900})
    page.clock.install(time=NOW)

    def acknowledged(target, done):
        """A credential the operator has acknowledged in ``done`` services."""
        return credential(target, "awaiting_ack") | {"acknowledged": done}

    load = {
        "id": uuid4(),
        "state": "running",
        "phase": "loading",
        "progress": Percentage(1234, 5678),
    }
    # The final load queued, then taken by a worker (#1916): Start the load
    # turns Done and the load's own box begins, in the same render.
    queued = load | {"state": "queued", "phase": "unspecified", "progress": None}
    claimed = load | {"phase": "starting"}
    both = [acknowledged("parishsoft", 2), acknowledged("google_workspace", 2)]
    # Each poll's answer: the step that moved on, its new state, and the
    # facts finalization_status would read for it.
    states = [
        ("parishsoft", "active", [credential("parishsoft", "testing")], None),
        (
            "parishsoft",
            "operator",
            [acknowledged("parishsoft", 1), credential("google_workspace", "staged")],
            None,
        ),
        ("configuration", "active", both, None),
        ("start", "active", both, queued),
        ("start", "done", both, claimed),
        ("source", "active", both, load),
    ]
    contexts = [
        finishing_context(
            checkpoint="validating",
            prepared=source is not None,
            credentials=credentials,
            source=source,
        )
        for _key, _state, credentials, source in states
    ]
    polls = answer(
        page, SETUP_FINISHING_STATUS, [finishing_status(c) for c in contexts]
    )
    page.goto(component_origin + "/setup-installation")
    page.evaluate(MARK)
    steps = "[data-finishing-step]"
    boxes = places(page, steps)
    assert len(boxes) == 6
    # Every row of the list has one height, so a wrapped second row is never
    # shorter than the first (#2145).
    assert len({round(height) for _x, _y, _w, height in boxes}) == 1, boxes
    # No time since confirmation and no line under the boxes (#2152): a
    # working step shows only the shared spinner in its box.
    assert page.locator("[data-finishing] > p").count() == 0
    assert "You confirmed setup" not in page.locator("[data-finishing]").inner_text()
    for count, (key, state, _credentials, _source) in enumerate(states, start=1):
        next_poll(page, polls, count)
        has_attribute(
            page.locator(f'[data-finishing-step="{key}"]'), "data-stage-state", state
        )
        assert same_place(places(page, steps), boxes), (key, state)
        active = page.locator(f'[data-finishing-step="{key}"] > .spinner')
        assert active.count() == (1 if state == "active" else 0), (key, state)
    # The running final load, its reserved forms included, names no task.
    final = page.locator('[data-finishing-step="source"]')
    assert "1,234" in final.inner_text()
    html = final.evaluate("node => node.outerHTML")
    assert "Task reference" not in html and str(load["id"]) not in html
    assert page.evaluate(MARKED) == "kept"


def test_a_failed_box_makes_only_its_own_row_taller(page, component_origin):
    """A failure's long explanation is never reserved (#1903), so a list with
    a failed box keeps natural rows: the row below it is not stretched to
    the failure's height (#2145)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/setup-credential-rejected")
    boxes = places(page, "[data-finishing-step]")
    failed = places(page, '[data-finishing-step][data-stage-state="failed"]')
    assert len(failed) == 1
    last_row = [box for box in boxes if box[1] == boxes[-1][1]]
    assert all(box[3] < failed[0][3] for box in last_row), boxes
