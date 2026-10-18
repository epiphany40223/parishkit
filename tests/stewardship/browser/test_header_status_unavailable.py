"""A failed header poll does not move the Admin page (#1643).

The Admin header polls the background-work counts and the Families-on-the-
form count. When a poll fails, the header says so ("Background status
unavailable; open the list to retry.", "Presence status unavailable; ...").
That sentence used to be an extra header item that appeared only on failure;
at desktop widths it wrapped onto its own row and pushed the page down under
the reader's pointer, which the UI conventions forbid (ui-conventions spec,
"No layout shift"). Each pill now holds both its counts and its unavailable
sentence in one grid cell, so a failure swaps the text without changing the
header's size.

The page is the Find a Family page (``find_family_components``) with the
long parish name: the busiest header, with the search box and both pills.
"""

import pytest

from .find_family_components import LONG
from .waits import eventually

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Viewport-relative boxes (no scroll offset: the reader's pointer is fixed to
# the window) of the header, the main content's heading and both pills.
POSITIONS = """() => {
  const box = (selector) => {
    const rect = document.querySelector(selector).getBoundingClientRect();
    return [rect.left, rect.top, rect.width, rect.height];
  };
  return {
    header: box(".site-header"),
    main: box("main h1"),
    background: box("[data-background-indicator]"),
    presence: box("[data-presence-indicator]"),
  };
}"""


@pytest.mark.parametrize("width", [320, 760, 1280])
def test_failed_header_polls_do_not_move_the_page(page, component_origin, width):
    """Both polls failing changes the pills' text, not anything's position."""
    from playwright.sync_api import expect

    from .conftest import NOW

    page.clock.install(time=NOW)
    # The presence poll runs as soon as the page loads: hold it so the page
    # is measured first, then fail it. The background poll first runs on its
    # 30-second timer, which the test fires.
    presence = []
    page.route(
        lambda url: "/admin/presence?format=count" in url,
        lambda route: presence.append(route),
    )
    page.route("**/admin/background/counts", lambda route: route.abort())
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(component_origin + LONG)
    eventually(page, "() => document.fonts.status === 'loaded'")
    background = page.locator("[data-background-unavailable]")
    unavailable = page.locator("[data-presence-unavailable]")
    expect(background).to_be_hidden()
    expect(unavailable).to_be_hidden()
    _wait_for(page, presence)
    before = page.evaluate(POSITIONS)

    presence[0].abort()
    page.clock.fast_forward(30000)
    expect(background).to_be_visible()
    expect(unavailable).to_be_visible()
    after = page.evaluate(POSITIONS)
    for name, edges in before.items():
        assert after[name] == pytest.approx(edges, abs=1), (name, before, after)
    # The failure sentence replaces the counts rather than sitting beside
    # them, so each pill's accessible name is the sentence alone.
    expect(page.locator("[data-background-total]")).to_be_hidden()
    expect(page.locator("[data-presence-count]")).to_be_hidden()
    expect(page.locator("[data-background-indicator]")).to_have_accessible_name(
        "Background status unavailable; open the list to retry."
    )
    expect(page.locator("[data-presence-indicator]")).to_have_accessible_name(
        "Presence status unavailable; open the list to retry."
    )


def _wait_for(page, held, timeout=10):
    """Wait (real time) until the held presence request has arrived."""
    import time

    deadline = time.monotonic() + timeout
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert len(held) == 1, held
