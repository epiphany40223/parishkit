"""The Admin wizards' action rows follow the stepper button row (#1936).

ui-conventions "Stepper button row": on every step of the setup wizard and
of Campaign setup (Go live included), Back sits at the row's left edge, the
step's one primary at its right edge and a secondary alternative, where a
step has one, centered; up to 48rem the middle action takes its own centered
line under Back and the primary. A slot a step does not fill keeps its
space, so Back and the primary sit in the same place on every step of a
flow. Each page is drawn from the real templates by the shared component
pages.
"""

import pytest

from .setup_back_path_components import (
    CREDENTIAL_CHECKED,
    PROGRESS_DONE,
    SOURCE_CHECKED,
    SOURCE_STALE,
)
from .setup_check_components import CHECK_FAILED, CHECK_WAITING
from .stepper_components import (
    CONTENT,
    EDITOR,
    EDITOR_DIRECT,
    LOADED,
    PATH,
    SUGGESTED,
)
from .test_components import axe_violations
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Each flow's pages, with the slots each one fills, so a slot a step leaves
# empty is checked to be empty as well.
SETUP = [
    (CREDENTIAL_CHECKED, {"back", "next"}),
    # Check parish data (#2201): Continue unavailable while the check waits,
    # then a link beside Check again; Check again alone after a failure.
    (CHECK_WAITING, {"back", "next"}),
    (SOURCE_CHECKED, {"back", "action", "next"}),
    (SOURCE_STALE, {"back", "next"}),
    (CHECK_FAILED, {"back", "next"}),
    # Loading parish data: Continue unavailable while the load runs, then
    # a link once it is done (#1968), in the same place.
    ("/setup-source-progress", {"back", "next"}),
    (PROGRESS_DONE, {"back", "next"}),
]
CAMPAIGN = [
    (PATH, {"back", "next"}),
    (CONTENT, {"back", "next"}),
    (SUGGESTED, {"back", "action", "next"}),
    (LOADED, {"back", "next"}),
    (EDITOR, {"back"}),
    (EDITOR_DIRECT, {"back", "next"}),
]
GO_LIVE = [
    ("/go-live-prepare-ready", {"action", "next"}),
    ("/go-live-confirm-not-started", {"next"}),
]
WIDTHS = [1280, 800, 760, 700, 641, 390, 375, 320]
# The widest screen (in CSS px, 48rem) on which the middle action takes a
# line of its own.
NARROW = 768


# One script turn reads the row and its slotted controls, so a scroll or a
# late layout between separate reads cannot skew the comparison.
MEASURE = """(row) => {
    const frame = row.getBoundingClientRect();
    const slots = {};
    for (const node of row.querySelectorAll(":scope > [data-nav-slot]")) {
        const box = node.getBoundingClientRect();
        const button = node.matches("a, button") ? node : node.querySelector("button");
        const style = getComputedStyle(button);
        slots[node.dataset.navSlot] = {
            left: box.left - frame.left, right: frame.right - box.right,
            top: box.top - frame.top, bottom: box.bottom - frame.top,
            center: box.left + box.width / 2 - (frame.left + frame.width / 2),
            width: box.width,
            height: button.getBoundingClientRect().height,
            lineHeight: parseFloat(style.lineHeight) || 0,
            padding: parseFloat(style.paddingTop) + parseFloat(style.paddingBottom),
            // The visible label, without a hidden ": <step>" suffix.
            label: button.firstChild.textContent.trim(),
        };
    }
    return {
        order: [...row.querySelectorAll(":scope > [data-nav-slot]")].map(
            (node) => node.dataset.navSlot),
        unslotted: [...row.children].filter((node) => !node.dataset.navSlot
            && node.getBoundingClientRect().width > 0).length,
        slots,
        width: frame.width,
        gap: parseFloat(getComputedStyle(row).columnGap),
    };
}"""


def measure(page, origin, path, selector):
    """Open ``path`` and measure its visible action row and slotted controls."""
    page.goto(origin + path)
    row = page.locator(selector).filter(visible=True).first
    visible(row)
    return row.evaluate(MEASURE)


def check_row(path, found, filled, width):
    """Assert one row's slots: order, edges, centering and one-line Back."""
    slots = found["slots"]
    assert set(slots) == filled, path
    assert found["unslotted"] == 0, path
    # The document order is the visual order: Back, action, Next.
    assert found["order"] == [s for s in ("back", "action", "next") if s in filled]
    if "back" in slots:
        back = slots["back"]
        assert abs(back["left"]) < 1, (path, back)
        # Back (one word) never wraps: one text line inside its padding and
        # border. A sub-page's Return to <step> may, on a narrow screen.
        if back["label"] == "Back":
            assert back["height"] < back["padding"] + 2 * back["lineHeight"], path

    if "next" in slots:
        assert abs(slots["next"]["right"]) < 1, (path, slots["next"])
    if "back" in slots and "next" in slots:
        assert abs(slots["back"]["top"] - slots["next"]["top"]) < 1, path
    if "action" in slots:
        action = slots["action"]
        if width <= NARROW:
            # Its own centered line, below Back and the primary.
            assert abs(action["center"]) < 1.5, (path, action)
            above = max(slots[s]["bottom"] for s in ("back", "next") if s in slots)
            assert action["top"] >= above, (path, action)
        else:
            # One line, between the two sides. The sides share the free
            # space equally, so the middle is centered, unless a side's
            # label is wider than its share: that side keeps its label on
            # one line instead.
            assert abs(action["top"]) < 1, (path, action)
            share = (found["width"] - action["width"]) / 2 - found["gap"]
            sides = [slots[s]["width"] for s in ("back", "next") if s in slots]
            if max(sides) <= share:
                assert abs(action["center"]) < 1.5, (path, action)
            # Nothing overlaps: the middle stays clear of either side.
            if "back" in slots:
                back = slots["back"]
                assert action["left"] >= back["left"] + back["width"], path
            if "next" in slots:
                after = slots["next"]
                assert action["right"] >= after["right"] + after["width"], path


def placed(found):
    """Back's and the primary's places in a row, compared across steps."""
    slots = found["slots"]
    return {
        name: (round(slots[name]["left"]), round(slots[name]["top"]))
        if name == "back"
        else (round(slots[name]["right"]), round(slots[name]["top"]))
        for name in ("back", "next")
        if name in slots
    }


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("width", WIDTHS)
def test_admin_rows_keep_back_left_action_centered_primary_right(
    page, component_origin, width, scheme
):
    """Every Admin wizard row places its slots the same way on every step."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.emulate_media(color_scheme=scheme)
    page.set_viewport_size({"width": width, "height": 900})
    for flow, selector in (
        (SETUP, ".setup-actions"),
        (CAMPAIGN, "[data-campaign-step-actions]"),
        (GO_LIVE, "[data-go-live-actions]"),
    ):
        places = []
        for path, filled in flow:
            found = measure(page, component_origin, path, selector)
            check_row(path, found, filled, width)
            places.append(placed(found))
            assert page.evaluate(
                "document.documentElement.scrollWidth"
                " <= document.documentElement.clientWidth"
            ), path
        # No button moves from one step of a flow to the next.
        for name in ("back", "next"):
            seen = {place[name] for place in places if name in place}
            assert len(seen) <= 1, (flow, name, seen)
    assert failures == []


@pytest.mark.parametrize("width", [320, 1280])
def test_a_middle_action_row_passes_the_scan(page, component_origin, axe_source, width):
    """The three-slot rows (a middle action) pass the accessibility scan."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (SUGGESTED, "/go-live-prepare-ready"):
        page.goto(component_origin + path)
        visible(page.locator("[data-nav-slot='action']").first)
        assert axe_violations(page, axe_source) == [], path
