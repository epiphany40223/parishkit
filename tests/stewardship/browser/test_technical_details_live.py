"""An open Technical details disclosure stays open as its poll updates it (#1586).

Send to chosen Families and Preview and test email list each recent test's
reference under Technical details, outside the polled status region. The
region carries a copy of the references (template[data-live-mirror]), and
live-status-v1.js puts it into the disclosure's list, so a poll that brings
a new reference never closes an open disclosure. Each test opens the
disclosure on a page whose test is still pending, lets a poll bring a newer
reference, and checks that the disclosure is the same open element, shows
the new reference, has not moved, and that the page was not reloaded and no
longer says to reload it. Both narrow (320 px) and wide (1280 px) pages.
"""

from uuid import UUID

import pytest

from .campaign_test_components import NEWER
from .campaign_test_components import SENT as MAIL_PENDING
from .family_test_components import PENDING, PENDING_NEWER
from .test_in_place import MARK, MARKED
from .waits import painted, same_place

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Each page: its pending address, the poll answer with a newer reference,
# the reference that answer brings, and the one it replaces.
PAGES = {
    "families": (PENDING, PENDING_NEWER, UUID(int=7000), UUID(int=7001)),
    "mail": (MAIL_PENDING, NEWER, UUID(int=5412), UUID(int=5411)),
}
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
    while a poll swaps in a newer reference, without a page reload."""
    from playwright.sync_api import expect  # imported late: collection needs no browser

    from .test_live_status_in_place import answer_reads

    address, newer, fresh, replaced = PAGES[name]
    page.set_viewport_size({"width": width, "height": 640})
    page.goto(component_origin + address)
    page.evaluate(MARK)
    details = page.locator("details.technical-details")
    expect(details).to_have_count(1)
    expect(details).not_to_contain_text("reload")
    summary = details.locator("summary")
    summary.click()
    expect(details.locator("code", has_text=str(replaced))).to_be_visible()
    # Mark the element itself: a redraw would replace it with a new one.
    details.evaluate("node => { node.dataset.kept = 'yes'; }")
    painted(page)
    before = summary.evaluate(PLACE)
    # Only now do polls (live-status-v1.js re-reads the page every 2 s at
    # first) bring the newer reference, so it lands while the disclosure is
    # open.
    reads = answer_reads(page, component_origin, address, newer)
    expect(details.locator("code", has_text=str(fresh))).to_be_visible(timeout=10000)
    expect(details.locator("code", has_text=str(replaced))).to_have_count(0)
    painted(page)
    assert reads
    assert details.evaluate("node => node.open && node.dataset.kept === 'yes'")
    assert same_place(summary.evaluate(PLACE), before)
    assert page.evaluate(MARKED) == "kept"
