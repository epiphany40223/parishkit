"""Reports whose figures are Updating show the new ones by themselves (#1586).

The Response dashboard and Campaign comparison audit every view, so they
are never re-read on a timer: their Updating notice sits in a figures status region
that live-status-v1.js watches through a passive status read, fast while the
figures are Updating. Once that read says newer figures are ready, ui-v1.js
re-reads the page's own address once, quietly, in place and passively
(X-ParishKit-Read: passive). Each test installs a fake clock, answers the
status reads and the page's re-read with fixtures, and sets a mark outside
every region, which only a full page load could lose.
"""

import pytest

from .comparison_components import DAY_5, NEWER, PAGE, REFUSED_UPDATING, UPDATING
from .conftest import NOW
from .report_components import FIGURES_NEWER, FIGURES_PENDING, REAL
from .test_in_place import MARK, MARKED, VIEWPORT, scroll_below
from .test_live_status_in_place import refresh_controls
from .waits import has_count, has_value, recorded, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The Response dashboard's fixture, its daily chart Updating.
DASHBOARD = "/participation"
# The Chart population's Current population segment (#1535).
CURRENT = '#participation-scope a[data-in-place="scope-current"]'
STATUS = "/admin/reports/responses/status/"
NOTICE = "[data-live-status=figures] .notice"
# The later page's all-Families Summary tile (report_components).
REFRESHED = "$982,657.00"
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


def answer(page, origin, matches, *fixtures):
    """Answer each fetch whose address ``matches`` (never a page load) with
    the next of ``fixtures``, the last one from then on. Returns the
    requests answered, as (address, X-ParishKit-Read header) pairs."""
    answered = []

    def fulfill(route):
        """Serve the next fixture to a fetch; let the page load through."""
        if route.request.resource_type == "document":
            route.continue_()
            return
        answered.append(
            (route.request.url, route.request.headers.get("x-parishkit-read"))
        )
        fixture = fixtures[min(len(answered), len(fixtures)) - 1]
        route.fulfill(response=route.fetch(url=origin + fixture, method="GET"))

    page.route(matches, fulfill)
    return answered


def statuses(page, origin, *fixtures):
    """Answer the figures status reads with ``fixtures`` (see answer)."""
    return answer(page, origin, lambda url: STATUS in url, *fixtures)


def rereads(page, origin, address, fixture):
    """Answer the page's own re-reads of ``address`` with ``fixture``."""
    return answer(
        page, origin, lambda url: url.split("#")[0] == origin + address, fixture
    )


def opened(page, origin, address):
    """Open ``address`` under a fake clock and mark the page."""
    page.set_viewport_size(VIEWPORT)
    page.clock.install(time=NOW)
    page.goto(origin + address)
    page.evaluate(MARK)
    assert refresh_controls(page).count() == 0
    assert "refresh this page" not in page.locator("main").inner_text().lower()


def test_response_dashboard_shows_its_newest_figures_by_itself(page, component_origin):
    """While the chart is Updating the status is read fast; once newer
    figures are ready the page re-reads itself once, passively and in place:
    the fresh statistics and no Updating notice, no reload, the scroll kept,
    focus left alone."""
    opened(page, component_origin, DASHBOARD)
    reads = statuses(page, component_origin, FIGURES_PENDING, FIGURES_NEWER)
    page_reads = rereads(page, component_origin, DASHBOARD, "/participation-later")
    visible(page.locator(NOTICE))
    assert (
        "appears here by itself once it is ready" in page.locator(NOTICE).inner_text()
    )
    offset = scroll_below(page, "#participation-chart")
    page.clock.fast_forward(2500)
    recorded(page, reads, 1)
    page.clock.fast_forward(2500)
    recorded(page, reads, 2)
    visible(page.locator("#response-summary .stat-value", has_text=REFRESHED))
    has_count(page.locator(NOTICE), 0)
    assert page.evaluate(MARKED) == "kept"
    assert abs(page.evaluate("window.scrollY") - offset) < 40
    assert page.evaluate("document.activeElement === document.body")
    assert page.url == component_origin + DASHBOARD
    assert len(page_reads) == 1
    # The re-read is passive (it never extends the sign-in); the status
    # reads are passive by themselves.
    assert page_reads[0][1] == "passive"


def test_figures_status_pauses_while_hidden_and_catches_up(page, component_origin):
    """A hidden tab's figures status is never read; shown again, it is read
    at once."""
    opened(page, component_origin, DASHBOARD)
    reads = statuses(page, component_origin, FIGURES_PENDING)
    page.evaluate(HIDE)
    page.clock.fast_forward(60 * 1000)
    page.evaluate("() => true")  # One round trip after the clock jump.
    assert reads == []
    page.evaluate(SHOW)
    recorded(page, reads, 1)


def test_reload_returns_to_the_same_view_and_goes_on_following(page, component_origin):
    """After Chart population is chosen in place, the address names it, so
    a browser reload shows the same view again (drawn by the server), and
    the reloaded page goes on following its figures."""
    opened(page, component_origin, REAL + "?timezone=America%2FLos_Angeles")
    page.locator(CURRENT).click()
    heading = page.get_by_role(
        "heading", name="Daily participation — Current population"
    )
    visible(heading)
    shown = page.url
    assert "scope=current" in shown
    reads = statuses(page, component_origin, FIGURES_PENDING)
    page.reload()
    assert page.url == shown
    visible(heading)
    page.clock.fast_forward(2500)
    recorded(page, reads, 1)


def test_campaign_comparison_shows_its_newest_figures_by_itself(page, component_origin):
    """Campaign comparison's Updating figures are replaced in place once newer
    ones are ready; the pledge distribution year the reader chose does not
    hold the update and is kept through it."""
    opened(page, component_origin, UPDATING)
    reads = statuses(page, component_origin, NEWER)
    page_reads = rereads(page, component_origin, UPDATING, PAGE)
    notice = page.locator("#comparison-report " + NOTICE)
    visible(notice)
    assert "appear here by themselves once they are ready" in notice.inner_text()
    year = page.get_by_label("Year", exact=True)
    chosen = year.locator("option").nth(1).get_attribute("value")
    year.select_option(chosen)
    page.clock.fast_forward(2500)
    recorded(page, reads, 1)
    recorded(page, page_reads, 1)
    has_count(notice, 0)
    has_value(page.get_by_label("Year", exact=True), chosen)
    assert page.evaluate(MARKED) == "kept"
    assert page_reads[0][1] == "passive"


def test_failed_follow_up_reads_back_off_to_the_status(page, component_origin):
    """A follow-up read that keeps failing (503) is audited each time, so it
    is retried a bounded number of times: twice more 5 s apart, then the
    page says it is not current and goes back to the passive status, read
    after 30 s and then every 60 s, and re-reads the page only when that
    status still says newer figures are ready."""
    opened(page, component_origin, DASHBOARD)
    reads = statuses(page, component_origin, FIGURES_NEWER)
    page_reads = []

    def unavailable(route):
        """Answer the page's own re-reads 503; let the page load through."""
        if route.request.resource_type == "document":
            route.continue_()
            return
        page_reads.append(route.request.url)
        route.fulfill(status=503, body="Unavailable")

    page.route(
        lambda url: url.split("#")[0] == component_origin + DASHBOARD,
        unavailable,
    )

    def quiet():
        """Let any read a timer just sent reach its route, so an extra
        read would show up in the counts asserted next."""
        page.wait_for_timeout(300)

    problem = page.locator("[data-reread-problem]")
    page.clock.fast_forward(2500)
    recorded(page, reads, 1)
    recorded(page, page_reads, 1)
    page.clock.fast_forward(5000)
    recorded(page, page_reads, 2)
    page.clock.fast_forward(5000)
    recorded(page, page_reads, 3)
    visible(problem)
    assert problem.inner_text() == "Couldn't check for updates — retrying."
    # Back on the passive status: nothing is read for 30 s.
    page.clock.fast_forward(25000)
    quiet()
    assert (len(reads), len(page_reads)) == (1, 3)
    page.clock.fast_forward(6000)
    recorded(page, reads, 2)
    recorded(page, page_reads, 4)
    # Then every 60 s, the line kept through the status's redraw.
    page.clock.fast_forward(50000)
    quiet()
    assert (len(reads), len(page_reads)) == (2, 4)
    visible(problem)
    page.clock.fast_forward(11000)
    recorded(page, reads, 3)
    recorded(page, page_reads, 5)
    visible(problem)
    assert page.evaluate(MARKED) == "kept"


def test_a_readers_own_read_is_never_marked_automatic(page, component_origin):
    """Only the page's own re-read is marked (#1620): X-ParishKit-Read is
    what marks an audited report view automatic, so a read the reader asks
    for, here Compare on day in place, never sends it."""
    opened(page, component_origin, PAGE)
    page.get_by_label("Compare on day").fill("5")
    with page.expect_request(
        lambda request: request.url.split("#")[0] == component_origin + DAY_5
    ) as asked:
        page.get_by_role("button", name="Compare", exact=True).click()
    page.wait_for_url(component_origin + DAY_5 + "#comparison-report")
    assert asked.value.headers.get("x-parishkit-read") is None
    assert page.evaluate(MARKED) == "kept"


def test_a_refused_day_is_never_followed(page, component_origin):
    """A Campaign comparison page answering a day it could not show is
    itself a refusal, so its own re-read would only be refused again: the
    Updating notice is shown but its figures status is never read."""
    opened(page, component_origin, REFUSED_UPDATING)
    reads = statuses(page, component_origin, NEWER)
    visible(page.locator("#comparison-report " + NOTICE))
    page.clock.fast_forward(60 * 1000)
    page.evaluate("() => true")  # One round trip after the clock jump.
    assert reads == []
    assert page.evaluate(MARKED) == "kept"
