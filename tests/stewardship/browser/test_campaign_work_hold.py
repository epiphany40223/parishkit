"""Campaign settings and Home update themselves when held work ends (#1619).

While background campaign work holds them, Campaign settings' held settings
and Home's Create the campaign are unavailable with a reason that never
says to reload. Each page follows the hold through the passive campaign
work status (live-status-v1.js). Once it ends, Campaign settings re-reads
its own address once, quietly, in place and passively (ui-v1.js, the
a[data-reread] follow-up), and Home takes its fresh panel from the status
itself. Each test installs a fake clock, answers the status reads and the
page's re-read with fixture pages (campaign_work_components), and marks the
page outside every region, which only a full page load could lose.
"""

import pytest

from .campaign_work_components import (
    HELD,
    HELD_FREED,
    HELD_LIVE,
    HELD_LIVE_FREED,
    HOME_HELD,
    HOME_PENDING,
    HOME_READY,
    WORK_DONE,
    WORK_PENDING,
)
from .conftest import NOW
from .test_in_place import MARK, MARKED, VIEWPORT, scroll_below
from .test_report_figures import HIDE, SHOW, answer
from .waits import available, greyed, has_text, recorded, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

STATUS = "/admin/campaign/work-status/"
ANNOUNCER = "[data-live-announcer=campaign-work]"


@pytest.fixture(autouse=True)
def settle_routes(page):
    """Let any routed read still being fetched finish before the page
    closes, so its fulfil never fails into the next test (#1850, #2311)."""
    yield
    page.unroute_all(behavior="wait")


def opened(page, origin, address):
    """Open ``address`` under a fake clock and mark the page."""
    page.set_viewport_size(VIEWPORT)
    page.clock.install(time=NOW)
    page.goto(origin + address)
    page.evaluate(MARK)
    assert "reload this page" not in page.locator("main").inner_text().lower()


def statuses(page, origin, *fixtures):
    """Answer the campaign work status reads with ``fixtures`` (see answer)."""
    return answer(page, origin, lambda url: STATUS in url, *fixtures)


def rereads(page, origin, address, fixture):
    """Answer the page's own re-reads of ``address`` with ``fixture``."""
    return answer(
        page, origin, lambda url: url.split("#")[0] == origin + address, fixture
    )


def until(page, requests, count):
    """Move the fake clock on until ``count`` reads were sent, at most a
    minute: the status backs off from 2 to 10 seconds."""
    for _ in range(12):
        if len(requests) >= count:
            break
        page.clock.fast_forward(5000)
        page.wait_for_timeout(100)
    recorded(page, requests, count)


def box(page, selector):
    """Where ``selector`` is drawn now on screen (in the viewport)."""
    return page.evaluate(
        """selector => {
            const r = document.querySelector(selector).getBoundingClientRect();
            return [r.left, r.top, r.width];
        }""",
        selector,
    )


def test_held_draft_opens_by_itself(page, component_origin):
    """A draft that background work holds: every setting disabled, its tip
    saying why without "reload". Once the status says the hold ended, the
    page re-reads itself once, passively and in place: the settings open,
    nothing moves, scroll and focus stay, the address is unchanged, and the
    end is announced politely."""
    opened(page, component_origin, HELD)
    name = page.get_by_label("Campaign name")
    greyed(name)
    reads = statuses(page, component_origin, WORK_PENDING, WORK_DONE)
    page_reads = rereads(page, component_origin, HELD, HELD_FREED)
    save = "#settings-form button[type=submit]"
    # Focus first: focusing scrolls the link into view.
    page.get_by_role("link", name="See Content history").focus()
    scroll_below(page, save)
    before = box(page, save)
    until(page, reads, 2)
    recorded(page, page_reads, 1)
    available(name)
    # The re-read is passive, so it never extends the sign-in.
    assert page_reads[0][1] == "passive"
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + HELD
    assert page.evaluate("document.activeElement.textContent") == (
        "See Content history"
    )
    # The reader keeps their place: what they were looking at stays where
    # it was on screen, within a pixel. (A locked field's label row, with
    # its "i" tip, is a pixel or two taller than an open one's; the
    # browser's scroll anchoring absorbs that above the reader's place.)
    after = box(page, save)
    assert same_place(after, before, tolerance=1), (before, after)
    has_text(
        page.locator(ANNOUNCER),
        "The background work has finished, so the settings it held can change now.",
    )
    # No status region is left to poll: the page drawn after the hold.
    assert page.locator("[data-live-status=campaign-work]").count() == 0
    page.clock.fast_forward(60 * 1000)
    page.wait_for_timeout(200)
    assert len(reads) == 2 and len(page_reads) == 1


def test_an_open_setting_being_edited_holds_the_re_read(page, component_origin):
    """A live campaign whose end date and Ministries the hold keeps, with
    its leader roles open: while the reader has changed a leader role and
    not reviewed it, the re-read waits, so the change is never replaced;
    once the role is put back, the page re-reads and the end date opens."""
    opened(page, component_origin, HELD_LIVE)
    form = page.locator("#settings-form")
    end = form.get_by_label("Campaign end date")
    greyed(end)
    roles = page.get_by_role("group", name="Ministry leader roles")
    roles.get_by_label("Staff").uncheck()
    reads = statuses(page, component_origin, WORK_DONE)
    page_reads = rereads(page, component_origin, HELD_LIVE, HELD_LIVE_FREED)
    until(page, reads, 1)
    # Each busy check waits 5 s; none of them reads the page.
    for _ in range(3):
        page.clock.fast_forward(5000)
        page.wait_for_timeout(100)
    assert page_reads == []
    assert not roles.get_by_label("Staff").is_checked()
    roles.get_by_label("Staff").check()
    page.clock.fast_forward(5000)
    recorded(page, page_reads, 1)
    available(end)
    assert page.evaluate(MARKED) == "kept"


def test_status_pauses_while_hidden_and_catches_up(page, component_origin):
    """A hidden tab's campaign work status is never read; shown again, it is
    read at once."""
    opened(page, component_origin, HELD)
    reads = statuses(page, component_origin, WORK_PENDING)
    page.evaluate(HIDE)
    page.clock.fast_forward(60 * 1000)
    page.evaluate("() => true")  # One round trip after the clock jump.
    assert reads == []
    page.evaluate(SHOW)
    recorded(page, reads, 1)


def test_reload_shows_the_same_held_page_and_goes_on_following(page, component_origin):
    """A browser reload while the hold lasts draws the same page, held, from
    the server, and the reloaded page goes on following the hold."""
    opened(page, component_origin, HELD)
    page.reload()
    assert page.url == component_origin + HELD
    greyed(page.get_by_label("Campaign name"))
    reads = statuses(page, component_origin, WORK_PENDING)
    until(page, reads, 1)


def test_home_offers_create_the_campaign_by_itself(page, component_origin):
    """Home's Create the campaign, unavailable while campaign work holds it,
    is offered where it stands once the status says the hold ended: the
    status's own answer is swapped in, Home is never read again (its view
    is audited), nothing below the panel moves, and the change is
    announced."""
    opened(page, component_origin, HOME_HELD)
    held = page.get_by_role("button", name="Create the campaign")
    greyed(held)
    reads = statuses(page, component_origin, HOME_PENDING, HOME_READY)
    home_reads = rereads(page, component_origin, HOME_HELD, HOME_HELD)
    below = "a[href$='/admin/parish/parishsoft-refresh/']"
    before = box(page, below)
    until(page, reads, 2)
    visible(page.get_by_role("link", name="Create the campaign"))
    assert home_reads == []
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + HOME_HELD
    assert same_place(box(page, below), before), (before, box(page, below))
    has_text(
        page.locator("[data-live-announcer=first-campaign]"),
        "Create the campaign is available now.",
    )
