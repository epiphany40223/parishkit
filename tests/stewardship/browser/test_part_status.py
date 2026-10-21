"""Parts an audited page could not read come in by themselves (#1863).

The Information request page's Contact details (replaced ParishSoft data)
and the Users page's ParishSoft Ministry leaders (a failed read) say so in
place, never asking for a reload. Their notice is a status region that
live-status-v1.js watches through a passive status read; once that read
says a re-read would show the part, ui-v1.js re-reads the page's own
address once, quietly, in place and passively (X-ParishKit-Read: passive).
Each test installs a fake clock, answers the status reads and the page's
re-read with fixtures, and sets a mark outside every region, which only a
full page load could lose.
"""

import pytest

from .conftest import NOW
from .information_components import (
    CONTACTS_READY,
    CONTACTS_WAITING,
    ITEM,
    REPLACED,
)
from .test_in_place import MARK, MARKED, VIEWPORT
from .test_report_figures import answer

# Autouse: answer the header's Background work poll, which the component
# server does not serve, so the minutes the clock moves draw nothing else.
from .test_system_health import background_counts  # noqa: F401
from .user_components import LEADERS_READY, LEADERS_UNREAD, LEADERS_WAITING, LIST
from .waits import has_count, recorded, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

CONTACTS_STATUS = "/admin/reports/information/contacts/status/"
LEADERS_STATUS = "/admin/users/leaders/status/"


@pytest.fixture(autouse=True)
def settle_routes(page):
    """Let any routed read still being fetched finish before the page
    closes, so its fulfil never fails into the next test (#1850)."""
    yield
    page.unroute_all(behavior="wait")


def opened(page, origin, address):
    """Open ``address`` under a fake clock and mark the page; it says
    nothing about reloading."""
    page.set_viewport_size(VIEWPORT)
    page.clock.install(time=NOW)
    page.goto(origin + address)
    page.evaluate(MARK)
    assert "reload" not in page.locator("main").inner_text().lower()


def test_replaced_contact_details_come_in_by_themselves(page, component_origin):
    """The notice's status is read at the usual pace; while the page's data
    is still in use nothing happens, and once newer data is in use the page
    re-reads itself once, passively and in place, and shows the details."""
    opened(page, component_origin, REPLACED)
    notice = page.locator("#information-contacts-unavailable")
    visible(notice)
    assert "appear here by themselves" in notice.inner_text()
    reads = answer(
        page,
        component_origin,
        lambda url: CONTACTS_STATUS in url,
        CONTACTS_WAITING,
        CONTACTS_READY,
    )
    page_reads = answer(
        page,
        component_origin,
        lambda url: url.split("#")[0] == component_origin + REPLACED,
        ITEM,
    )
    page.clock.fast_forward(2500)
    recorded(page, reads, 1)
    assert reads[0][0].endswith(CONTACTS_STATUS + "?shown=3")
    visible(notice)
    assert page_reads == []
    page.wait_for_timeout(500)
    page.clock.fast_forward(2500)
    recorded(page, reads, 2)
    visible(page.locator("#information-contact h3", has_text="Anna Example"))
    has_count(notice, 0)
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + REPLACED
    assert len(page_reads) == 1
    # The re-read is passive: it never extends the sign-in.
    assert page_reads[0][1] == "passive"


def test_unreadable_leaders_come_in_by_themselves(page, component_origin):
    """The leaders' notice is checked once a minute, not faster; once its
    status is ready the page re-reads itself once, passively and in place,
    and the leaders table with its Import buttons replaces the notice."""
    opened(page, component_origin, LEADERS_UNREAD)
    notice = page.locator("#leaders-unavailable")
    visible(notice)
    assert "appear here by themselves" in notice.inner_text()
    reads = answer(
        page,
        component_origin,
        lambda url: LEADERS_STATUS in url,
        LEADERS_WAITING,
        LEADERS_READY,
    )
    page_reads = answer(
        page,
        component_origin,
        lambda url: url.split("#")[0] == component_origin + LEADERS_UNREAD,
        LIST,
    )
    # Fixed 60 s pace: nothing is read in the first ten seconds.
    page.clock.fast_forward(10_000)
    page.evaluate("() => true")  # One round trip after the clock jump.
    assert reads == []
    page.clock.fast_forward(51_000)
    recorded(page, reads, 1)
    assert reads[0][0].endswith(LEADERS_STATUS + "?shown=3&retry=1")
    visible(notice)
    # Let its answer arrive, so the next check is scheduled from now.
    page.wait_for_timeout(500)
    page.clock.fast_forward(61_000)
    recorded(page, reads, 2)
    visible(page.locator("#leader-import button", has_text="Import all"))
    has_count(notice, 0)
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + LEADERS_UNREAD
    assert len(page_reads) == 1
    assert page_reads[0][1] == "passive"
