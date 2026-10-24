"""The refresh schedule's seven-day grid in real browsers (#632 slice 4).

The grid is read-only and redrawn by the live check with the rest of the
preview. These tests check what only a browser can: a full refresh is drawn
as a bar (and stored quick updates, removed by #1147, draw nothing), the
current quarter hour is marked, the grid fits a laptop page, and on a phone
it scrolls sideways inside its own box with the day labels held in place,
never the page itself.
"""

import pytest

from .refresh_schedule_components import PRODUCTION
from .test_components import axe_violations
from .test_refresh_schedule_editor import answering
from .waits import eventually, has_class, recorded, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

GRID = ".schedule-grid-scroll"
ROWS = ".schedule-grid-row:not(.schedule-grid-hours)"


def mark_size(page, selector, pseudo):
    """The width and height of a cell's ``::before`` or ``::after`` mark."""
    return page.evaluate(
        """([selector, pseudo]) => {
            const style = getComputedStyle(document.querySelector(selector), pseudo);
            return [parseFloat(style.width), parseFloat(style.height)];
        }""",
        [selector, pseudo],
    )


def test_the_grid_marks_full_refreshes_and_fits_the_page(page, component_origin):
    """Seven day rows; a full refresh is drawn as a bar in its cell."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/refresh-schedule")
    grid = page.locator(GRID)
    visible(grid)
    assert page.locator(ROWS).count() == 7
    assert grid.get_attribute("role") == "img"
    assert "list below" in grid.get_attribute("aria-label")
    full_width, full_height = mark_size(page, f"{ROWS} .grid-full", "::before")
    assert full_height > 0 and full_width > 0
    # Today's row holds the one "now" cell; the legend names it.
    assert page.locator(f"{ROWS} .grid-now").count() == 1
    assert page.locator(f"{ROWS}").first.locator(".grid-now").count() == 1
    visible(page.locator(".schedule-grid-legend").get_by_text("Now"))
    # At laptop width the whole day fits: nothing scrolls sideways.
    assert grid.evaluate("box => box.scrollWidth <= box.clientWidth")
    # The grid comes before the list it pictures.
    assert grid.evaluate(
        "box => !!(box.compareDocumentPosition(document.querySelector("
        "'details.schedule-day')) & Node.DOCUMENT_POSITION_FOLLOWING)"
    )


def test_on_a_phone_the_grid_scrolls_in_its_box_with_the_days_held(
    page, component_origin, axe_source
):
    """Below 600 pixels the grid scrolls sideways itself; the page never does."""
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + "/refresh-schedule")
    grid = page.locator(GRID)
    visible(grid)
    assert grid.evaluate("box => box.scrollWidth > box.clientWidth")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    label = page.locator(f"{ROWS} .schedule-grid-day").first
    before = label.bounding_box()["x"]
    grid.evaluate("box => { box.scrollLeft = box.scrollWidth; }")
    eventually(page, "document.querySelector('.schedule-grid-scroll').scrollLeft > 0")
    assert abs(label.bounding_box()["x"] - before) < 0.5
    assert axe_violations(page, axe_source) == []


def test_the_live_check_redraws_the_grid(page, component_origin):
    """A changed rule is drawn in the grid from the server's answer."""
    requests = answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    first_row = page.locator(ROWS).first
    before = first_row.locator(".grid-full").count()
    assert before == len(PRODUCTION["full_refresh_times"])
    # The 08:00 full time moves to 09:00: still one a slot, in another cell.
    cell = first_row.locator("span[title^='08:00 to 08:15']")
    assert "grid-full" in cell.get_attribute("class")
    page.locator('[name="rules-1-at"]').fill("09:00")
    recorded(page, requests, 1)
    has_class(first_row.locator("span[title^='09:00 to 09:15']"), "grid-full")
    assert "grid-full" not in (
        first_row.locator("span[title^='08:00 to 08:15']").get_attribute("class")
    )


def test_stored_quick_updates_draw_no_mark(page, component_origin):
    """Production's stored quarter-hour quick updates no longer run (#1147):
    the grid has no mark for them and its legend does not name one."""
    answering(page, PRODUCTION)
    page.goto(component_origin + "/refresh-schedule")
    visible(page.locator(GRID))
    assert page.locator(f"{ROWS} .grid-quick").count() == 0
    assert page.locator(".schedule-grid-legend .grid-quick").count() == 0
    assert "uick" not in page.locator(".schedule-grid-legend").inner_text()
