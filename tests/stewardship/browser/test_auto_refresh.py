"""Pages that update themselves (UX conventions, #1586).

Background work and Families on the form now carry no Refresh link: every
30 seconds while the tab is visible, ui-v1.js re-reads the address shown,
quietly and in place (a[data-reread-every]). Each test installs a fake clock
so it can move 30 seconds at once, routes the page's re-reads to a fresh
version (in_place_components), and sets a mark outside every region, which
only a full page load could lose. A browser reload lands on the same view,
and a reload after an in-place POST never sends it again.
"""

import pytest

from .conftest import NOW
from .in_place_components import (
    BACKGROUND,
    BACKGROUND_25_LATER,
    BACKGROUND_ALL,
    BACKGROUND_ALL_LATER,
    BACKGROUND_LATER,
    FORM,
    PRESENCE,
    PRESENCE_LATER,
)
from .test_in_place import MARK, MARKED, VIEWPORT, count_requests, scroll_below
from .test_live_status_in_place import answer_reads, refresh_controls

# Autouse: answer the header's Background work poll, which the component
# server does not serve. Past 30 s a failed poll draws "Background status
# unavailable", which wraps the header at 1280 px on Linux fonts and moves
# the whole page down (52 px), as the scroll checks here would see.
from .test_system_health import background_counts  # noqa: F401
from .waits import eventually, has_text, hidden, recorded, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# A little more than the 30-second pace.
TICK = 31 * 1000
# Make the page believe its tab was hidden, then shown again.
HIDE = """() => {
    Object.defineProperty(document, "visibilityState",
        {configurable: true, get: () => "hidden"});
    document.dispatchEvent(new Event("visibilitychange"));
}"""
SHOW = """() => {
    delete document.visibilityState;
    document.dispatchEvent(new Event("visibilitychange"));
}"""
PROBLEM = "[data-reread-problem]"
# No in-place read is in flight (each marks its region busy until settled).
IDLE = "() => !document.querySelector('[aria-busy=true]')"


def counts(active):
    """The background page's work-count line with ``active`` tasks running."""
    return (
        f"Running: {active} · Waiting to start: 0 · Waiting to try again: 0 · "
        "Stopped unexpectedly: 0"
    )


def opened(page, origin, address):
    """Open ``address`` under a fake clock and mark the page."""
    page.set_viewport_size(VIEWPORT)
    page.clock.install(time=NOW)
    page.goto(origin + address)
    page.evaluate(MARK)
    assert refresh_controls(page).count() == 0


def test_background_work_updates_itself_in_place(page, component_origin):
    """Thirty seconds on, the table and the work counts are re-read once, in
    place: no reload, the scroll position and focus on a task link kept,
    nothing announced and the address unchanged."""
    opened(page, component_origin, BACKGROUND)
    reads = answer_reads(page, component_origin, BACKGROUND, BACKGROUND_LATER)
    offset = scroll_below(page, "#table")
    link = page.locator("#table tbody th a").first
    link.focus()
    page.clock.fast_forward(TICK)
    has_text(page.locator("#background-counts"), counts(2))
    assert page.evaluate(MARKED) == "kept"
    assert abs(page.evaluate("window.scrollY") - offset) < 40
    assert page.evaluate("document.activeElement.textContent") == link.text_content()
    assert page.url == component_origin + BACKGROUND
    assert page.get_by_role("status").filter(has_text="refreshed").count() == 0
    assert len(reads) == 1


def test_families_on_the_form_update_themselves(page, component_origin):
    """The rows, the count and the time outside the table follow the list
    by themselves."""
    opened(page, component_origin, PRESENCE)
    reads = answer_reads(page, component_origin, PRESENCE, PRESENCE_LATER)
    assert page.locator("#table tbody tr").count() == 1
    page.clock.fast_forward(TICK)
    has_text(
        page.locator("#presence-count"),
        "Families with the Family form open in their browser within the last "
        "90 seconds: 2",
    )
    assert page.locator("#table tbody tr").count() == 2
    assert page.evaluate(MARKED) == "kept"
    assert len(reads) == 1


def test_rereads_pause_while_hidden_and_catch_up_when_shown(page, component_origin):
    """A hidden tab is never read; shown again, the page catches up at once."""
    opened(page, component_origin, BACKGROUND)
    reads = answer_reads(page, component_origin, BACKGROUND, BACKGROUND_LATER)
    page.evaluate(HIDE)
    page.clock.fast_forward(3 * TICK)
    page.evaluate("() => true")  # One round trip after the clock jump.
    assert reads == []
    page.evaluate(SHOW)
    has_text(page.locator("#background-counts"), counts(2))
    assert len(reads) == 1


def test_focus_on_the_bottom_next_stays_there(page, component_origin):
    """The table draws its navigator twice, so the bottom Next has a twin
    above with the same address and text: a re-read puts focus back on the
    bottom one, never the first match."""
    opened(page, component_origin, BACKGROUND)
    reads = answer_reads(page, component_origin, BACKGROUND, BACKGROUND_LATER)
    page.locator("#table .table-nav").nth(1).get_by_role("link", name="Next").focus()
    page.clock.fast_forward(TICK)
    has_text(page.locator("#background-counts"), counts(2))
    eventually(page, IDLE)
    assert page.evaluate(FOCUSED_NEXT) == 1
    assert len(reads) == 1


# Which of the table's Next links has focus (0 the top one), or -1.
FOCUSED_NEXT = """() => [...document.querySelectorAll('#table [data-table-next]')]
    .indexOf(document.activeElement)"""


def test_focus_on_a_rows_technical_details_stays_on_that_row(page, component_origin):
    """Every row has its own Technical details summary with the same text:
    focus on the second row's stays on that row's fresh summary."""
    opened(page, component_origin, BACKGROUND_ALL)
    reads = answer_reads(page, component_origin, BACKGROUND_ALL, BACKGROUND_ALL_LATER)
    details = page.locator("#table details.technical-details")
    assert details.count() == 2
    second = details.nth(1).get_attribute("id")
    details.nth(1).locator("summary").focus()
    page.clock.fast_forward(TICK)
    has_text(page.locator("#background-counts"), counts(2))
    eventually(page, IDLE)
    assert page.evaluate("document.activeElement.tagName") == "SUMMARY"
    assert page.evaluate("document.activeElement.closest('details').id") == second
    assert len(reads) == 1


def test_rereads_wait_while_the_reader_is_mid_edit(page, component_origin):
    """Focus in a text-entry field in the table (the navigator's Page box)
    holds the update; so does a value typed there but not applied after the
    reader moves on. Once the box shows what the page drew, updates go on."""
    opened(page, component_origin, BACKGROUND)
    reads = answer_reads(page, component_origin, BACKGROUND, BACKGROUND_LATER)
    box = page.locator("#table [data-page-number]").first
    box.focus()
    page.clock.fast_forward(TICK)
    page.evaluate("() => true")
    assert reads == []
    box.fill("2")
    page.evaluate("document.activeElement.blur()")
    page.clock.fast_forward(TICK)
    page.evaluate("() => true")
    assert reads == []
    box.fill("1")
    page.evaluate("document.activeElement.blur()")
    page.clock.fast_forward(TICK)
    has_text(page.locator("#background-counts"), counts(2))
    assert len(reads) == 1


def test_a_focused_control_outside_the_table_does_not_hold_updates(
    page, component_origin
):
    """Task state sits outside every region a re-read replaces, so focus on
    it never pauses the updates."""
    opened(page, component_origin, BACKGROUND)
    reads = answer_reads(page, component_origin, BACKGROUND, BACKGROUND_LATER)
    page.get_by_label("Task state").focus()
    page.clock.fast_forward(TICK)
    has_text(page.locator("#background-counts"), counts(2))
    assert page.evaluate("document.activeElement.id") == "task-state"
    assert len(reads) == 1


def test_updates_go_on_after_a_rows_per_page_change(page, component_origin):
    """A Rows per page choice is applied in place and hands focus to the
    fresh choice; that applied, focused choice does not pause the updates."""
    opened(page, component_origin, BACKGROUND)
    size = page.locator("#table [data-page-size]").first
    size.select_option("25")
    eventually(page, IDLE)
    eventually(
        page,
        "() => document.activeElement.matches('#table [data-page-size]')"
        " && document.activeElement.value === '25'",
    )
    shown = page.url.split("#")[0]
    assert "size=25" in shown
    reads = answer_reads(
        page, component_origin, shown[len(component_origin) :], BACKGROUND_25_LATER
    )
    page.clock.fast_forward(TICK)
    has_text(page.locator("#background-counts"), counts(2))
    assert page.evaluate(MARKED) == "kept"
    assert len(reads) == 1


def test_failed_rereads_say_so_and_never_navigate(page, component_origin):
    """Reads that fail say so only after three in a row, and the page stays;
    the next read that works clears the line. A refused read (signed out)
    stops the reads and says so."""
    opened(page, component_origin, BACKGROUND)
    failed = []

    def fail(route):
        """Fail every re-read with no answer; let the page load through."""
        if route.request.resource_type == "document":
            route.continue_()
            return
        failed.append(route.request.url)
        route.abort()

    page.route(lambda url: url.split("#")[0] == component_origin + BACKGROUND, fail)
    for count in (1, 2, 3):
        # Nothing is said for the first two failures.
        assert page.locator(f"{PROBLEM}:not([hidden])").count() == 0
        page.clock.fast_forward(TICK)
        recorded(page, failed, count)
        eventually(page, IDLE)
    has_text(page.locator(PROBLEM), "Couldn't check for updates — retrying.")
    assert page.evaluate(MARKED) == "kept"
    page.unroute_all()
    reads = answer_reads(page, component_origin, BACKGROUND, BACKGROUND_LATER)
    page.clock.fast_forward(TICK)
    has_text(page.locator("#background-counts"), counts(2))
    hidden(page.locator(PROBLEM))
    assert len(reads) == 1

    page.unroute_all()
    refused = []

    def refuse(route):
        """Refuse every re-read, as for a sign-in that ended."""
        if route.request.resource_type == "document":
            route.continue_()
            return
        refused.append(route.request.url)
        route.fulfill(status=403, content_type="text/html", body="<p>No</p>")

    page.route(lambda url: url.split("#")[0] == component_origin + BACKGROUND, refuse)
    page.clock.fast_forward(TICK)
    has_text(
        page.locator(PROBLEM),
        "Couldn't check for updates. Reload the page or sign in again.",
    )
    page.clock.fast_forward(3 * TICK)
    page.evaluate("() => true")
    assert len(refused) == 1
    assert page.evaluate(MARKED) == "kept"


def test_reload_keeps_the_view_and_the_updates(page, component_origin):
    """After Next, the address names page 2, so a browser reload shows page
    2 again (drawn by the server) and the page goes on updating that view."""
    opened(page, component_origin, BACKGROUND)
    page.locator("#table .table-nav").first.get_by_role("link", name="Next").click()
    has_text(page.locator("#table tbody th[scope=row] a"), "ParishSoft data refresh 2")
    shown = page.url
    assert "page=2" in shown
    page.reload()
    assert page.url == shown
    has_text(page.locator("#table tbody th[scope=row] a"), "ParishSoft data refresh 2")
    reads = []
    page.on(
        "request",
        lambda request: (
            reads.append(request.url)
            if request.headers.get("x-requested-with") == "fetch"
            else None
        ),
    )
    page.clock.fast_forward(TICK)
    recorded(page, reads, 1)
    assert "page=2" in reads[0]


def test_reload_after_an_in_place_save_sends_nothing_again(page, component_origin):
    """An in-place Save follows its redirect and puts that GET address in the
    address bar, so a browser reload shows the saved page again without a
    resubmit prompt and without posting the form again."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + FORM)
    posts = count_requests(page, "POST", "/in-place-form/")
    page.locator("#save").click()
    has_text(page.locator("#saved-count"), "Saved 1 times")
    assert page.url == component_origin + FORM + "?saved=1#saved"
    page.reload()
    visible(page.locator("#saved-count"))
    has_text(page.locator("#saved-count"), "Saved 1 times")
    assert page.url == component_origin + FORM + "?saved=1#saved"
    assert len(posts) == 1
