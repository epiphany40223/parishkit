"""Refresh on the go-live and Production pages acts in place (#519 PR 7b-2).

Go-live readiness, Testing cleanup, Prepare Family links and Production
activation are served at their real addresses (go_live_components), where
their Refresh links lead, and each in-place read is answered with a later
version of the page. Each test sets a mark outside every region, which only
a full page load could lose, scrolls the link below the fold with the mouse
wheel (a scripted scroll can be undone by Firefox, #1088), and checks the
mark, that the page did not jump back to the top, the fresh content, focus,
the announcement and that exactly one in-place read was sent.
"""

import pytest

from .go_live_components import CLEANUP, GO_LIVE, LINKS, PRODUCTION
from .test_in_place import MARK, MARKED
from .test_live_status_in_place import answer_reads
from .waits import has_text, hidden, painted, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Short enough that every page's Refresh link starts below the fold.
VIEWPORT = {"width": 1280, "height": 320}
LINK = "a[data-in-place='refresh']"

# wheel_below and in_place_reads match PR 7b's helpers (#1232,
# test_live_status_refresh_in_place.py); once both have merged they can
# move to one shared module.


def wheel_below(page, selector, *, attempts=20):
    """Wheel the page until ``selector`` sits 200 px below the viewport's top,
    or as far as the page scrolls, and holds there; return that scrollY.

    The wheel, not a script: Firefox can re-apply an earlier scroll into view
    at a later refresh and undo a scripted one (#1088). A wheel turn can stop
    short, so the page is wheeled until it holds across two settled checks.
    Firefox also moves a single wheel event by at most about a viewport, so
    a long page takes several turns.
    """
    where = (
        "Math.min(document.documentElement.scrollHeight"
        " - document.documentElement.clientHeight,"
        f" scrollY + document.querySelector({selector!r}).getBoundingClientRect().top"
        " - 200)"
    )
    there = f"Math.abs(({where}) - scrollY) <= 1"
    # Over the main content, so the wheel scrolls the page, not the menu.
    page.mouse.move(900, 200)
    for _ in range(attempts):
        page.mouse.wheel(0, page.evaluate(f"({where}) - scrollY"))
        painted(page)
        if not page.evaluate(there):
            continue
        painted(page)
        if page.evaluate(there):
            offset = page.evaluate("scrollY")
            assert offset > 0, "the link must start below the fold for this test"
            return offset
    raise AssertionError(f"the page did not stay below {selector}")


def in_place_reads(page):
    """Collect every read ui-v1.js sends in place (it marks them with
    X-Requested-With: fetch; live-status-v1.js's polls carry no such mark)."""
    seen = []
    page.on(
        "request",
        lambda request: (
            seen.append(request.url)
            if request.headers.get("x-requested-with") == "fetch"
            else None
        ),
    )
    return seen


def refresh(page, component_origin, address, fresh, link, message, *, before=None):
    """Open ``address``, mark it, run ``before`` (if any), and click its
    Refresh link named ``link``, whose read is answered with the page at
    ``fresh``; wait for ``message``. Returns the in-place reads.

    Checks that the page neither reloaded nor jumped back up, that the link
    stays in view with focus, and that exactly one in-place read was sent.
    """
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + address)
    page.evaluate(MARK)
    if before:
        before()
    # Only reads of the Refresh link's own address are answered; it may carry
    # a query (the preparation history's page), so match its href.
    target = page.locator(LINK).get_attribute("href").split("#")[0]
    answer_reads(page, component_origin, target, fresh)
    offset = wheel_below(page, LINK)
    reads = in_place_reads(page)
    page.locator(f"{LINK}:text-is({link!r})").click()
    has_text(page.get_by_role("status").filter(has_text=message), message)
    assert page.evaluate(MARKED) == "kept"
    # The page never jumps back up: it stays where the reader was (or as far
    # down as a shorter page still goes), and the link stays in view.
    lowest = page.evaluate(
        "document.documentElement.scrollHeight - document.documentElement.clientHeight"
    )
    assert page.evaluate("scrollY") >= min(offset, lowest) - 40
    assert page.evaluate(
        "selector => { const box = document.querySelector(selector)"
        ".getBoundingClientRect(); return box.top >= 0"
        " && box.bottom <= document.documentElement.clientHeight; }",
        LINK,
    )
    assert page.evaluate("document.activeElement.textContent") == link
    assert len(reads) == 1
    return reads


def test_readiness_refresh_is_in_place(page, component_origin):
    """Go-live readiness's Refresh brings checks that now pass in place."""
    refresh(
        page,
        component_origin,
        GO_LIVE,
        "/go-live-fresh",
        "Refresh readiness preview",
        "Readiness preview refreshed.",
    )
    visible(page.get_by_text("The settings and data checks passed.", exact=False))
    hidden(page.get_by_text("Save exactly one initial invitation schedule."))
    assert page.url == component_origin + GO_LIVE + "#readiness-preview"


def test_cleanup_refresh_keeps_details_open_and_updates_them(page, component_origin):
    """Testing cleanup's Refresh brings the finished cleanup in place; an
    open Technical details stays open with its updated values."""
    details = page.locator("details.technical-details")

    def open_details():
        """Open Technical details before Refresh, showing the failed state."""
        details.locator("summary").click()
        visible(details.get_by_text("cleanup_failed", exact=True))

    refresh(
        page,
        component_origin,
        CLEANUP,
        "/go-live-cleanup-fresh",
        "Refresh cleanup progress",
        "Progress refreshed.",
        before=open_details,
    )
    visible(page.get_by_role("link", name="Prepare inactive Family links"))
    hidden(page.get_by_role("button", name="Retry failed cleanup from its checkpoints"))
    visible(details.get_by_text("cleanup_complete", exact=True))
    assert details.evaluate("node => node.open")


def test_links_refresh_keeps_the_page_and_is_in_place(page, component_origin):
    """Prepare Family links' Refresh re-reads the shown history page and
    brings the prepared links in place; paging the history afterwards is
    still an ordinary load."""
    reads = refresh(
        page,
        component_origin,
        LINKS,
        "/go-live-links-fresh",
        "Refresh preparation progress",
        # The history's navigator sits in the region, so its rows are read
        # out too, as after a table refresh.
        "Progress refreshed. Showing 1–1 · Page 1",
    )
    assert "page=1" in reads[0] and "size=25" in reads[0]
    visible(page.get_by_role("link", name="Continue to Confirm Production"))
    hidden(page.get_by_role("button", name="Retry failed preparation"))
    # Paging the history stays an ordinary load (live-status-v1.js owns its
    # panels), even from the navigator the Refresh swapped in.
    # Not waited on to "load": Firefox does not always fire it for a page
    # that keeps polling, and the navigation itself is what is under test.
    with page.expect_navigation(wait_until="domcontentloaded"):
        page.locator("select[data-page-size]").first.select_option("50")
    assert "size=50" in page.url
    # The new page carries no marker. A retrying locator assertion, not an
    # evaluate: Firefox can still be tearing down the old page's context when
    # domcontentloaded fires, and an evaluate then fails with "Execution
    # context was destroyed".
    from playwright.sync_api import expect  # imported late: collection needs no browser

    expect(page.locator("h1[data-mark]")).to_have_count(0)


def test_production_refresh_is_in_place(page, component_origin):
    """Production activation's Refresh brings the finished mail preparation
    in place, and Cancel go-live goes with the state that offered it."""
    refresh(
        page,
        component_origin,
        PRODUCTION,
        "/production-progress-fresh",
        "Refresh Production progress",
        "Progress refreshed.",
        before=lambda: visible(page.get_by_role("link", name="Cancel go-live")),
    )
    visible(
        page.get_by_role("heading", name="Initial campaign mail preparation complete")
    )
    hidden(page.get_by_role("link", name="Cancel go-live"))
