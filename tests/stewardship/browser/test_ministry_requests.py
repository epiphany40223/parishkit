"""Ministry requests (#1133): the review worklist (#1684) with its By Ministry
counts, across engines and widths. A Ministry leader's page has a short help
and no By Ministry (#2395)."""

import pytest
from django.urls import reverse

from .followup_components import (
    COUNTED,
    GATED_REQUESTS,
    HUB,
    LEADER,
    REQUESTS,
    SEARCHED,
)
from .test_campaign_comparison import COLLISIONS
from .test_charts import assert_clean, watch
from .test_response_dashboard import FITTED
from .waits import eventually, painted, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
AXE = """async () => (await axe.run(document, {
  runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
})).violations.map(({id,impact}) => ({id,impact}))"""
JOINS = "51 join requests for Example <Ministry>"


def open_by_ministry(page):
    """Open the By Ministry disclosure, which starts closed (#1653) unless
    this browser remembers it open from an earlier visit."""
    if not page.locator("details.by-ministry").evaluate("panel => panel.open"):
        page.locator("details.by-ministry > summary").click()
    visible(page.locator("#by-ministry"))


@pytest.mark.parametrize("width", [320, 1280])
def test_ministry_requests_fit_the_window_and_pass_axe(
    page, component_origin, axe_source, width
):
    """Staff's page, a one-Ministry leader's and the gated page fit the
    window with no sideways page scroll and no accessibility violation."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (REQUESTS, LEADER, GATED_REQUESTS):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert page.evaluate(AXE) == [], path


def test_by_ministry_marks_for_staff_and_none_for_a_leader(page, component_origin):
    """Staff see a By Ministry table with each Ministry's ParishSoft and
    campaign marks (#1133, #1135); a zero count is plain text. A Ministry
    leader's page has no By Ministry and a short help, with neither mark
    (#2395); its About this page control still sits on the heading's line."""
    page.goto(component_origin + REQUESTS)
    open_by_ministry(page)
    section = page.locator("#by-ministry")
    rows = section.locator("tbody tr")
    assert rows.count() == 4
    assert "Not in ParishSoft" in rows.first.inner_text()
    assert "Removed from this campaign" in rows.nth(1).inner_text()
    link = section.get_by_role("link", name=JOINS)
    assert link.get_attribute("href") == COUNTED + "#table"
    # Example has no leave request and Another Ministry nothing to review.
    assert section.get_by_role("link", name="0 leave requests for").count() == 0
    assert section.get_by_role("link").count() == 4
    page.goto(component_origin + LEADER)
    assert page.locator("details.by-ministry, #by-ministry").count() == 0
    summary = page.locator("details.about-page > summary")
    summary.click()
    about = page.locator(".about-page-body")
    visible(about)
    text = " ".join(about.inner_text().split())
    assert text.startswith("This page lists the requests Families made to join")
    assert "Review one at a time does the same work" in text
    assert "Live join and leave requests" not in text
    # Neither the marks nor the help about them (the queue's own rows keep
    # their Removed badge).
    for hidden in ("Not in ParishSoft", "Removed from this campaign"):
        assert hidden not in text
    heading = page.locator("h1").bounding_box()
    control = summary.bounding_box()
    assert control["y"] < heading["y"] + heading["height"]


def test_leaders_column_shows_each_ministrys_status_to_staff_only(
    page, component_origin
):
    """Staff's By Ministry ends with Leaders: a status indicator per Ministry
    (word and icon, Working or Warning), one line high, never a link; the
    page help explains it. A Ministry leader gets neither (#1594)."""
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + REQUESTS)
    open_by_ministry(page)
    section = page.locator("#by-ministry")
    headers = section.locator("thead th")
    assert headers.last.inner_text() == "Leaders"
    cells = section.locator("tbody tr td:last-child .leader-status")
    assert cells.count() == 4
    assert [cells.nth(i).inner_text().strip() for i in range(4)] == [
        "2 signed in",
        "No leader",
        "No user yet",
        "Not signed in yet",
    ]
    assert "status-working" in cells.first.get_attribute("class")
    assert "status-warning" in cells.nth(1).get_attribute("class")
    # The icon differs in shape per state and is hidden from assistive
    # technology: the word carries the state.
    assert cells.first.locator("svg.level-icon-ok[aria-hidden=true]").count() == 1
    assert cells.nth(1).locator("svg.level-icon-warning[aria-hidden=true]").count() == 1
    assert section.locator("tbody .leader-status a").count() == 0
    # One line, so the row keeps the height of its counts.
    # The longest words (No user yet, Not signed in yet) stay one line and
    # fit the page at 320 pixels as well.
    for width in (1280, 320):
        page.set_viewport_size({"width": width, "height": 900})
        for index in range(4):
            cell = cells.nth(index).bounding_box()
            row = section.locator("tbody tr").nth(index).bounding_box()
            assert cell["height"] < 2 * 16 and cell["height"] <= row["height"]
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.set_viewport_size({"width": 1280, "height": 900})
    about = page.locator(".about-page").text_content()
    assert "Leaders says whether anyone besides Staff" in about
    page.goto(component_origin + LEADER)
    assert page.locator("#by-ministry, .leader-status").count() == 0
    assert "Leaders says whether" not in page.locator(".about-page").text_content()


def test_a_count_filters_the_table_in_place(page, component_origin):
    """A By Ministry count applies its Ministry and request to the table
    with no reload, no jump and no layout shift; the filter bar then shows
    them, focus stays on the count and the address names the view (#1133)."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 700})
    errors = watch(page)
    page.goto(component_origin + REQUESTS)
    open_by_ministry(page)
    page.evaluate("window.__requestsMarker = 1")
    link = page.locator("#by-ministry").get_by_role("link", name=JOINS)
    # Scroll the page as a reader would, the count still in view (it now
    # starts near the top, #1653): the page must keep that place.
    page.mouse.move(640, 350)
    page.mouse.wheel(0, max(link.bounding_box()["y"] - 350, 120))
    eventually(page, "() => window.scrollY > 0")
    # Linux WebKit animates the wheel: measure where the scroll lands,
    # not its first frame.
    painted(page)
    scrolled = page.evaluate("window.scrollY")
    before = link.bounding_box()
    apply = page.get_by_role("button", name="Apply filters").bounding_box()
    link.click()
    expect(page.locator("#table tbody tr")).to_have_count(1)
    expect(page.get_by_label("Request", exact=True)).to_have_value("join")
    expect(page.get_by_label("Stage", exact=True)).to_have_value("all")
    expect(page.get_by_label("Ministry", exact=True)).to_have_value("9")
    expect(page.get_by_label("Search Member or Ministry name")).to_have_value("")
    assert page.evaluate("window.__requestsMarker") == 1
    assert page.url.endswith(COUNTED + "#table")
    # Focus stays on the count, found again in the redrawn By Ministry.
    eventually(
        page,
        "() => document.activeElement?.dataset.inPlace === 'count-9-joining'",
    )
    eventually(page, FITTED)
    assert abs(page.evaluate("window.scrollY") - scrolled) < 2
    after = page.locator("#by-ministry").get_by_role("link", name=JOINS)
    assert abs(after.bounding_box()["y"] - before["y"]) < 2
    moved = page.get_by_role("button", name="Apply filters").bounding_box()
    assert abs(moved["y"] - apply["y"]) < 2
    assert_clean(page, errors)


def test_applied_filters_redraw_the_worklist_in_place(page, component_origin):
    """Apply filters swaps in the worklist's rows and Stage counts without a
    reload, and By Ministry above does not move (#1133, #1684)."""
    from playwright.sync_api import expect

    errors = watch(page)
    answer = page.request.get(component_origin + SEARCHED).text()
    page.goto(component_origin + REQUESTS)
    open_by_ministry(page)
    expect(page.locator("#table tbody tr")).to_have_count(2)
    page.evaluate("window.__requestsMarker = 1")
    where = (
        "() => document.querySelector('#by-ministry')"
        ".getBoundingClientRect().top + scrollY"
    )
    held = page.evaluate(where)
    page.route(
        "**" + reverse("admin:ministry_report"),
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    page.get_by_role("button", name="Apply filters").click()
    expect(page.locator("#table tbody tr")).to_have_count(1)
    expect(page.get_by_label("Stage", exact=True).locator("option").first).to_have_text(
        "To review (1)"
    )
    assert page.evaluate("window.__requestsMarker") == 1
    assert abs(page.evaluate(where) - held) < 2
    assert_clean(page, errors)


def test_the_worklist_has_no_totals_or_chart(page, component_origin):
    """The Join and Leave requests tiles and the Follow-up progress chart are
    gone (#1676, folded into #1684): nothing sits between the worklist's
    filters and its table, at a phone or a desktop width, and the page draws
    no chart."""
    errors = watch(page)
    for width in (320, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(component_origin + REQUESTS)
        assert page.locator("#ministry-totals, .stat-tile, [data-chart]").count() == 0
        between = page.evaluate(
            """() => {
                const filters = document.querySelector("#table-filters");
                const table = document.querySelector("#table");
                let node = filters.nextElementSibling, found = [];
                while (node && node !== table) {
                    // The reserved notice line sits there (R38 finding 10).
                    if (!node.matches(".worklist-notice")) found.push(node.tagName);
                    node = node.nextElementSibling;
                }
                return found;
            }"""
        )
        assert between == []
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.evaluate(COLLISIONS) == []
    assert_clean(page, errors)


@pytest.mark.parametrize(
    ("path", "hub"), [(REQUESTS, True), (LEADER, False)], ids=["staff", "leader"]
)
def test_member_opens_the_hub_and_ministry_the_request(
    page, component_origin, path, hub
):
    """A Member's name opens the Family hub for Staff and plain text for a
    Ministry leader; the Ministry opens the request's page for both (R38
    finding 13)."""
    page.goto(component_origin + path)
    first = page.locator("#table tbody tr").first
    name = first.locator("th[scope=row]")
    if hub:
        assert name.locator("a").get_attribute("href") == HUB
    else:
        assert name.locator("a").count() == 0
    ministry = first.locator("td a[href*='/follow-up/']")
    assert ministry.count() == 1
