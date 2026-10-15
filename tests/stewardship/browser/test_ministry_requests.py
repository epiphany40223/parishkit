"""Ministry requests (#1133): the follow-up queue with its totals, By Ministry
counts and Follow-up packet, across engines and widths."""

import re
from urllib.parse import quote

import pytest
from django.urls import reverse

from .followup_components import (
    COUNTED,
    GATED_REQUESTS,
    LEADER,
    NOTHING,
    PACKET_LIMIT,
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


def test_by_ministry_marks_and_one_line_for_one_ministry(page, component_origin):
    """Staff see a By Ministry table with each Ministry's ParishSoft and
    campaign marks; a leader of one Ministry sees one line with no marks
    (#1133, #1135). A zero count is plain text."""
    page.goto(component_origin + REQUESTS)
    section = page.locator("#by-ministry")
    rows = section.locator("tbody tr")
    assert rows.count() == 2
    assert "Not in ParishSoft data" in rows.first.inner_text()
    assert "No longer in this campaign" in rows.nth(1).inner_text()
    link = section.get_by_role("link", name=JOINS)
    assert link.get_attribute("href") == COUNTED + "#table"
    # Example has no leave request and Another Ministry nothing unresolved.
    assert section.get_by_role("link", name="0 leave requests for").count() == 0
    assert section.get_by_role("link").count() == 4
    page.goto(component_origin + LEADER)
    section = page.locator("#by-ministry")
    assert section.locator("table").count() == 0
    line = section.locator(".by-ministry-line")
    assert " ".join(line.inner_text().split()) == (
        "Example <Ministry>: 51 join requests · 0 leave requests · 40 unresolved"
    )
    visible(line.get_by_role("link", name=JOINS))
    # Neither the marks nor the help about them (the queue's own rows keep
    # their Removed badge).
    text = section.text_content() + page.locator(".about-page").text_content()
    for hidden in ("Not in ParishSoft data", "No longer in this campaign"):
        assert hidden not in text


def test_a_count_filters_the_table_in_place(page, component_origin):
    """A By Ministry count applies its Ministry and request to the table
    with no reload, no jump and no layout shift; the filter bar then shows
    them, focus stays on the count and the address names the view (#1133)."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 700})
    errors = watch(page)
    page.goto(component_origin + REQUESTS)
    page.evaluate("window.__requestsMarker = 1")
    link = page.locator("#by-ministry").get_by_role("link", name=JOINS)
    # Scroll the count to the window's middle, as a reader would: the page
    # must keep that place.
    page.mouse.move(640, 350)
    page.mouse.wheel(0, link.bounding_box()["y"] - 350)
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
    expect(page.get_by_label("Status", exact=True)).to_have_value("any")
    expect(page.get_by_label("Ministry", exact=True)).to_have_value("9")
    expect(page.get_by_label("Search Member or Ministry name")).to_have_value("")
    expect(page.locator("#ministry-totals .stat-value").nth(1)).to_have_text("0")
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


def test_applied_filters_redraw_the_totals_in_place(page, component_origin):
    """Apply filters swaps in the view's totals and draws the fresh chart
    without a reload, and By Ministry below does not move (#720, #1133)."""
    from playwright.sync_api import expect

    errors = watch(page)
    answer = page.request.get(component_origin + SEARCHED).text()
    page.goto(component_origin + REQUESTS)
    totals = page.locator("#ministry-totals")
    expect(totals.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    page.evaluate("window.__requestsMarker = 1")
    offset = """(() =>
      document.querySelector("#by-ministry").getBoundingClientRect().top
      - document.querySelector("#ministry-totals").getBoundingClientRect().top)()"""
    held = page.evaluate(offset)
    page.route(
        "**" + reverse("admin:ministry_report"),
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    page.get_by_role("button", name="Apply filters").click()
    expect(totals.locator(".stat-value").nth(1)).to_have_text("9")
    expect(totals.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    assert page.evaluate("window.__requestsMarker") == 1
    eventually(page, FITTED)
    assert abs(page.evaluate(offset) - held) < 2
    ticks = totals.locator("svg .role-axis-label text:has(tspan)")
    assert ticks.first.locator("tspan").all_text_contents() == [
        "Resolved or closed",
        "51 out of 60 (85%)",
    ]
    assert_clean(page, errors)


def test_an_empty_search_keeps_the_chart_so_a_count_moves_nothing(
    page, component_origin
):
    """A search that matches nothing still draws the chart, its bars at
    zero, so a By Ministry count clicked next brings no chart back to push
    By Ministry down under the pointer (no layout shift, #736)."""
    from playwright.sync_api import expect

    page.set_viewport_size({"width": 1280, "height": 700})
    errors = watch(page)
    answer = page.request.get(component_origin + NOTHING).text()
    page.goto(component_origin + REQUESTS)
    totals = page.locator("#ministry-totals")
    expect(totals.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    # Only the Apply filters post is answered with the empty search.
    page.route(
        "**" + reverse("admin:ministry_report"),
        lambda route: (
            route.fulfill(body=answer, content_type="text/html")
            if route.request.method == "POST"
            else route.continue_()
        ),
    )
    page.get_by_role("button", name="Apply filters").click()
    expect(page.locator("#table tbody tr")).to_have_text(
        [re.compile("No matching requests")]
    )
    expect(totals.locator(".stat-value")).to_have_text(["0", "0"])
    expect(totals.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    eventually(page, FITTED)
    link = page.locator("#by-ministry").get_by_role("link", name=JOINS)
    # Scroll the count to the window's middle, as a reader would, so the
    # click itself scrolls nothing.
    page.mouse.move(640, 350)
    page.mouse.wheel(0, link.bounding_box()["y"] - 350)
    eventually(page, "() => window.scrollY > 0")
    # Linux WebKit animates the wheel: measure where the scroll lands,
    # not its first frame.
    painted(page)
    before = link.bounding_box()
    link.click()
    expect(totals.locator(".stat-value")).to_have_text(["51", "0"])
    expect(totals.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    eventually(page, FITTED)
    after = page.locator("#by-ministry").get_by_role("link", name=JOINS)
    assert abs(after.bounding_box()["y"] - before["y"]) < 2
    assert_clean(page, errors)


def test_totals_draw_follow_up_progress_bars(page, component_origin):
    """The totals are two tiles, then follow-up progress bars (#720).

    The chart draws under the CSP with no script error and is named and
    focusable; each bar's axis label is its name over its value, so there is
    no Exact values table or visible caption. At 320 and 1280 pixels the
    page fits its window with no text drawn over other text.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + REQUESTS)
    totals = page.locator("#ministry-totals")
    expect(totals.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    assert totals.locator(".stat-tile").all_inner_texts() == [
        "Join requests\n40",
        "Leave requests\n11",
    ]
    view = totals.locator("[data-chart-view]")
    assert view.get_attribute("aria-label") == "Follow-up progress"
    assert view.get_attribute("tabindex") == "0"
    assert totals.locator("figcaption").bounding_box()["width"] <= 1
    ticks = totals.locator("svg .role-axis-label text:has(tspan)")
    assert [tick.locator("tspan").all_text_contents() for tick in ticks.all()] == [
        ["Resolved or closed", "11 out of 62 (17.7%)"],
        ["Unresolved", "51 out of 62 (82.3%)"],
    ]
    expect(totals.get_by_text("Exact values")).to_have_count(0)
    for width in (320, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        eventually(page, FITTED)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.evaluate(COLLISIONS) == []
    assert_clean(page, errors)


def test_packet_request_posts_its_selection(page, component_origin):
    """The packet posts its chosen Ministries; nothing ticked means every
    Ministry in scope, and it waits while other campaign work runs."""
    page.goto(component_origin + REQUESTS)
    packet = page.locator("form", has_text="Ministries in the packet")
    assert packet.locator("input[name=ministries]:checked").count() == 0
    page.route(
        "**" + reverse("admin:ministry_packet"),
        lambda route: route.fulfill(body="Queued"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Queue follow-up packet").click()
    assert "ministries=" not in sent.value.post_data
    assert "format=pdf" in sent.value.post_data
    # Let the routed answer finish loading before the next navigation,
    # which it would otherwise interrupt (#623).
    visible(page.get_by_text("Queued", exact=True))
    page.goto(component_origin + REQUESTS)
    page.get_by_role("checkbox", name="Example <Ministry>", exact=True).check()
    page.get_by_label("Also include resolved and withdrawn requests").check()
    page.get_by_label("Packet format").select_option("xlsx")
    zone = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    page.route(
        "**" + reverse("admin:ministry_packet"),
        lambda route: route.fulfill(body="Queued"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Queue follow-up packet").click()
    body = sent.value.post_data
    assert body.count("ministries=") == 1 and "ministries=9" in body
    assert "history=yes" in body and "format=xlsx" in body
    assert "browser_timezone=" + quote(zone, safe="") in body
    assert "?" not in sent.value.url
    visible(page.get_by_text("Queued", exact=True))
    page.goto(component_origin + GATED_REQUESTS)
    assert page.get_by_role("button", name="Queue follow-up packet").is_disabled()


def test_packet_waits_while_too_many_ministries_are_ticked(page, component_origin):
    """Past the packet limit (#1332), Queue waits with the count as its
    reason, in a reserved line that moves nothing; unticking one, or every
    one, brings Queue back."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + PACKET_LIMIT)
    queue = page.get_by_role("button", name="Queue follow-up packet")
    hint = page.locator("#ministry-packet-hint")
    boxes = page.locator("#ministry-packet-choices input[name=ministries]")
    assert boxes.count() == 2 and queue.is_enabled()
    assert "ministry-packet-hint" in queue.get_attribute("aria-describedby").split()
    # Page coordinates: ticking a box scrolls it into view.
    where = (
        "e => { const r = e.getBoundingClientRect();"
        " return [r.left + scrollX, r.top + scrollY]; }"
    )
    place = queue.evaluate(where)
    height = hint.bounding_box()["height"]
    boxes.nth(0).check()
    assert queue.is_enabled() and hint.inner_text() == ""
    boxes.nth(1).check()
    assert queue.is_disabled()
    assert hint.inner_text() == (
        "2 Ministries are ticked, more than 1. Untick some, or untick them all "
        "to include every Ministry."
    )
    assert queue.evaluate(where) == place
    boxes.nth(0).uncheck()
    assert queue.is_enabled() and hint.inner_text() == ""
    assert hint.bounding_box()["height"] == height
    # Over the limit again, then every box unticked: all Ministries, allowed.
    boxes.nth(0).check()
    assert queue.is_disabled()
    boxes.nth(0).uncheck()
    boxes.nth(1).uncheck()
    assert page.locator("#ministry-packet-choices input:checked").count() == 0
    assert queue.is_enabled() and hint.inner_text() == ""
    assert not failures
