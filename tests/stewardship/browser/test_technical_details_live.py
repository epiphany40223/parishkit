"""An open Technical details disclosure stays open as its poll updates it.

Send to chosen Families and Preview and test email list each recent test's
reference under Technical details (#1586); Production activation, the Mail
queue and System health's Version and services tile list internal states
(#2016). Each disclosure sits outside the polled status region. The region
carries a copy of its list (template[data-live-mirror]), and
live-status-v1.js puts it into the disclosure's list, so a poll that brings
new values never closes an open disclosure. Each test opens the disclosure
on a page that is still polling, lets a poll bring a new value, and checks
that the disclosure is the same open element, shows the new value, has not
moved, and that the page was not reloaded and never says its values are as
of when it was opened or to reload it. Both narrow (320 px) and wide
(1280 px) pages. The Background work task page's disclosure (#2019) has
its own test at the end, since what its poll changes there is a heartbeat
(a time, not a code) and its status region must not change with it.
"""

import re
from uuid import UUID

import pytest

from .campaign_test_components import NEWER
from .campaign_test_components import SENT as MAIL_PENDING
from .family_test_components import PENDING, PENDING_NEWER
from .go_live_components import PRODUCTION_LATER, PRODUCTION_PREPARING
from .live_status_components import TASK_BEAT_LATER, TASK_BEAT_PAGE, TASK_BEAT_STATUS
from .send_progress_components import STEADY as QUEUE_STEADY
from .send_progress_components import STEADY_REMINDER as QUEUE_REMINDER
from .send_progress_components import STEADY_STATUS as QUEUE_STATUS
from .system_health_components import STEADY as HEALTH_STEADY
from .system_health_components import STEADY_STATUS as HEALTH_STATUS
from .system_health_components import STEADY_TESTING as HEALTH_TESTING
from .test_in_place import MARK, MARKED
from .waits import painted, same_place

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Each page: its address, the address its polls read, the poll answer with
# a new value, the value that answer brings, the one it replaces, and the
# tile to open first (System health's disclosure is in a tile's band).
PAGES = {
    "families": (
        PENDING,
        PENDING,
        PENDING_NEWER,
        UUID(int=7000),
        UUID(int=7001),
        None,
    ),
    "mail": (MAIL_PENDING, MAIL_PENDING, NEWER, UUID(int=5412), UUID(int=5411), None),
    "production": (
        PRODUCTION_PREPARING,
        PRODUCTION_PREPARING,
        PRODUCTION_LATER,
        "reports",
        "families",
        None,
    ),
    "queue": (QUEUE_STEADY, QUEUE_STATUS, QUEUE_REMINDER, "reminder", "initial", None),
    "health": (
        HEALTH_STEADY,
        HEALTH_STATUS,
        HEALTH_TESTING,
        "testing",
        "production",
        "version",
    ),
}
# What a disclosure that did not follow its poll used to say.
OPENED = re.compile("reload|as of when", re.IGNORECASE)
# The disclosure's place on the page (not the viewport), so a poll's
# change of the page's height cannot hide a move.
PLACE = (
    "node => { const box = node.getBoundingClientRect();"
    " return [box.left, box.top + scrollY, box.width, box.height]; }"
)


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("name", sorted(PAGES))
def test_open_technical_details_take_a_poll_in_place(
    page, component_origin, name, width
):
    """The open disclosure keeps its element, its open state and its place
    while a poll swaps in a new value, without a page reload."""
    from playwright.sync_api import expect  # imported late: collection needs no browser

    from .test_live_status_in_place import answer_reads

    address, polled, newer, fresh, replaced, tile = PAGES[name]
    fresh, replaced = (re.compile(f"^{value}$") for value in (fresh, replaced))
    page.set_viewport_size({"width": width, "height": 640})
    page.goto(component_origin + address)
    page.evaluate(MARK)
    if tile:
        page.locator(f"#health-{tile} [data-health-toggle]").click()
    details = page.locator("details.technical-details")
    expect(details).to_have_count(1)
    expect(details).not_to_contain_text(OPENED)
    summary = details.locator("summary")
    summary.click()
    expect(details.locator("code", has_text=replaced)).to_be_visible()
    # Off the page's content, so a target that waits while the pointer is
    # over it (System health's tile grid) takes the poll.
    page.mouse.move(0, 0)
    # Mark the element itself: a redraw would replace it with a new one.
    details.evaluate("node => { node.dataset.kept = 'yes'; }")
    painted(page)
    before = summary.evaluate(PLACE)
    # Only now do polls (every 2 to 10 s, by page) bring the new value, so
    # it lands while the disclosure is open.
    reads = answer_reads(page, component_origin, polled, newer)
    expect(details.locator("code", has_text=fresh)).to_be_visible(timeout=25000)
    expect(details.locator("code", has_text=replaced)).to_have_count(0)
    expect(details).not_to_contain_text(OPENED)
    painted(page)
    assert reads
    assert details.evaluate("node => node.open && node.dataset.kept === 'yes'")
    assert same_place(summary.evaluate(PLACE), before)
    assert page.evaluate(MARKED) == "kept"


@pytest.mark.parametrize("width", [320, 1280])
def test_health_details_wait_while_the_pointer_is_over_the_grid(
    page, component_origin, width
):
    """System health's list is data-live-mirror-hold: while the pointer is
    over the tile grid a poll's new value does not swap in (nothing moves
    under the pointer), and it does once the pointer leaves."""
    from playwright.sync_api import expect  # imported late: collection needs no browser

    from .test_live_status_in_place import answer_reads

    address, polled, newer, fresh, replaced, tile = PAGES["health"]
    fresh, replaced = (re.compile(f"^{value}$") for value in (fresh, replaced))
    page.set_viewport_size({"width": width, "height": 640})
    page.goto(component_origin + address)
    page.locator(f"#health-{tile} [data-health-toggle]").click()
    details = page.locator("details.technical-details")
    summary = details.locator("summary")
    summary.click()
    expect(details.locator("code", has_text=replaced)).to_be_visible()
    # The pointer rests on the disclosure, inside the tile grid.
    summary.hover()
    reads = answer_reads(page, component_origin, polled, newer)
    # Let at least one poll arrive and (not) apply, then a settle margin.
    for _ in range(100):
        if reads:
            break
        page.wait_for_timeout(250)
    assert reads
    page.wait_for_timeout(1500)
    expect(details.locator("code", has_text=replaced)).to_be_visible()
    expect(details.locator("code", has_text=fresh)).to_have_count(0)
    # Off the grid, the held copy goes in.
    page.mouse.move(0, 0)
    expect(details.locator("code", has_text=fresh)).to_be_visible(timeout=25000)
    expect(details.locator("code", has_text=replaced)).to_have_count(0)
    assert details.evaluate("node => node.open")


@pytest.mark.parametrize("width", [320, 1280])
def test_task_heartbeat_alone_updates_details_not_the_region(
    page, component_origin, width
):
    """A poll that brings only a later heartbeat updates the task page's
    open Technical details in place: the same open element, in the same
    place, with no reload and no "as of when" wording. Its status region
    stays as it was: the copy is lasting (data-live-mirror-lasting), so a
    heartbeat is not a change to the region and never redraws or
    re-announces it (#2019)."""
    from playwright.sync_api import expect  # imported late: collection needs no browser

    from .test_live_status_in_place import answer_reads

    page.set_viewport_size({"width": width, "height": 640})
    page.goto(component_origin + TASK_BEAT_PAGE)
    page.evaluate(MARK)
    details = page.locator("details.technical-details")
    expect(details).to_have_count(1)
    expect(details).not_to_contain_text(OPENED)
    summary = details.locator("summary")
    summary.click()
    beat = details.locator("time").last
    expect(beat).to_be_visible()
    before = beat.get_attribute("datetime")
    # Mark the region's contents and the disclosure: a redraw of either
    # would replace the marked element with a new one.
    heading = page.locator('[data-live-status="task"] h2')
    heading.evaluate("node => { node.dataset.kept = 'yes'; }")
    details.evaluate("node => { node.dataset.kept = 'yes'; }")
    painted(page)
    place = summary.evaluate(PLACE)
    reads = answer_reads(page, component_origin, TASK_BEAT_STATUS, TASK_BEAT_LATER)
    expect(beat).not_to_have_attribute("datetime", before, timeout=25000)
    expect(details).not_to_contain_text(OPENED)
    painted(page)
    assert reads
    assert same_place(summary.evaluate(PLACE), place)
    assert details.evaluate("node => node.open && node.dataset.kept === 'yes'")
    assert heading.evaluate("node => node.dataset.kept === 'yes'")
    assert page.evaluate(MARKED) == "kept"
