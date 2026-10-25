"""Admin table time cells stay on one line (#2179).

ui-conventions' Data tables rule says date cells never wrap. #2176 fixed the
Timelines; these are a representative few of the other Admin tables whose
compact browser-local times sat in a cell without ``nowrap``, so a long
neighbouring column pushed the time's AM/PM onto a second line.
"""

import re

import pytest

from .automation_components import ACCESS
from .in_place_components import BACKGROUND
from .past_campaign_components import DONE as PAST_CAMPAIGNS
from .signed_in_components import PAGE as SIGNED_IN

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Automation access shows its ended table only when asked; Past campaigns'
# imported years each hold an Imported time.
PAGES = (SIGNED_IN, ACCESS + "?ended=yes", PAST_CAMPAIGNS, BACKGROUND)


@pytest.mark.parametrize("path", PAGES)
def test_each_time_cell_is_one_line_and_the_page_never_scrolls_sideways(
    page, component_origin, path
):
    """Each table time keeps its AM/PM on its line; the table scrolls in its box."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + path)
    # Open any closed disclosure, so every time is laid out and has client
    # rects to count.
    page.locator("main details").evaluate_all(
        "boxes => boxes.forEach(b => { b.open = true; })"
    )
    times = page.locator("main table tbody td time[data-local-instant][data-compact]")
    # Wait for the script's browser-local form, which ends in AM or PM.
    expect(times.first).to_have_text(re.compile(r" [AP]M$"))
    # Real rows can carry long labels, names or reasons, which squeeze the
    # time column; the fixtures' are short, so fill every other cell.
    page.locator("main table tbody td:not(:has(time))").evaluate_all(
        "cells => cells.forEach(c => { c.textContent = 'word '.repeat(80); })"
    )
    # An inline element has one client rect per line box it occupies.
    assert times.count() > 0
    assert (
        times.evaluate_all("nodes => nodes.map(n => n.getClientRects().length)")
        == [1] * times.count()
    )
    page.set_viewport_size({"width": 320, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
