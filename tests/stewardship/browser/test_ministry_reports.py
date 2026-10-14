"""Native private search and accessible scoped Ministry report controls."""

from urllib.parse import quote

import pytest
from django.urls import reverse

from .test_campaign_comparison import COLLISIONS
from .test_charts import assert_clean, watch
from .test_response_dashboard import FITTED
from .waits import eventually, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_ministry_reports_mobile_keyboard_and_accessibility(
    page, component_origin, axe_source, width
):
    """Reports stay readable and private without requiring a JavaScript UI."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in ("/ministry-summary", "/ministry-summary-leader", "/ministry-gated"):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    page.goto(component_origin + "/ministry-summary")
    assert page.get_by_text("Example <Ministry>", exact=True).count() >= 1
    search = page.get_by_label("Search Ministry name")
    search.focus()
    page.keyboard.press("Tab")
    # No Ministry activity filter sits after the search any more (#1267);
    # where focus lands next depends on the engine (WebKit skips buttons).
    assert page.locator(":focus").get_attribute("name") != "activity"
    assert page.locator("[name=activity]").count() == 0
    heading = page.locator("th[data-sort-column='ministry']")
    assert heading.get_attribute("aria-sort") == "ascending"


def test_ministry_marks_are_shown_to_staff_only(page, component_origin):
    """Staff and Administrators see each Ministry's "Not in ParishSoft data"
    mark; a Ministry leader sees none, nor the help about them (#1135).
    Nobody gets a Ministry activity filter or an Active/Inactive mark any
    more (#1267)."""
    page.goto(component_origin + "/ministry-summary")
    assert page.get_by_label("Ministry activity").count() == 0
    row = page.locator("tbody tr").first
    assert "Not in ParishSoft data" in row.inner_text()
    assert "Inactive" not in row.inner_text()
    page.goto(component_origin + "/ministry-summary-leader")
    visible(page.get_by_label("Search Ministry name"))
    assert page.get_by_label("Ministry activity").count() == 0
    assert page.locator("tbody tr th").first.inner_text() == "Example <Ministry>"
    text = page.locator("main").text_content()
    for hidden in ("Ministry activity", "Inactive", "Not in ParishSoft data"):
        assert hidden not in text
    assert "No longer in this campaign" not in text


def test_ministry_sort_heading_posts_privately(page, component_origin):
    """A heading re-sorts through a POST that keeps the private search."""
    page.goto(component_origin + "/ministry-summary")
    page.route(
        "**" + reverse("admin:ministry_report"),
        lambda route: route.fulfill(body="Sorted"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.locator("th[data-sort-column='ministry'] button").click()
    assert "sort=name_desc" in sent.value.post_data
    assert "search=Example" in sent.value.post_data
    assert "ministry=" not in sent.value.post_data
    assert "page=" not in sent.value.post_data and "?" not in sent.value.url
    visible(page.get_by_text("Sorted", exact=True))


def test_ministry_counts_link_the_queue(page, component_origin):
    """A count links to the filtered follow-up queue by name, and a zero
    count is plain text (#1133). The retired lists' Contact details
    controls are gone (#1157): contact details are on each request's page."""
    page.goto(component_origin + "/ministry-summary")
    queue = reverse("admin:ministry_followup")
    joining = page.get_by_role("link", name="51 join requests for Example <Ministry>")
    assert joining.get_attribute("href") == (
        f"{queue}?ministry=9&action=join&state=any"
    )
    unresolved = page.get_by_role(
        "link", name="40 unresolved requests for Example <Ministry>"
    )
    assert unresolved.get_attribute("href") == f"{queue}?ministry=9"
    assert page.get_by_role("link", name="leave requests").count() == 0
    visible(page.get_by_text("0 leave requests", exact=True))
    assert page.get_by_role("button", name="Contact details").count() == 0
    assert page.locator("form[action$='/joining/']").count() == 0


def test_ministry_search_posts_privately(page, component_origin):
    """Private search submits only in a POST body."""
    page.goto(component_origin + "/ministry-summary")
    page.get_by_label("Search Ministry name").fill("Private name")
    page.route(
        "**" + reverse("admin:ministry_report"),
        lambda route: route.fulfill(body="Filtered"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert "search=Private+name" in sent.value.post_data
    assert "Private" not in sent.value.url and "?" not in sent.value.url
    visible(page.get_by_text("Filtered", exact=True))


def test_ministry_packet_request_posts_its_selection(page, component_origin):
    """The multi-Ministry selection posts its choices; detail pages do not offer it."""
    page.goto(component_origin + "/ministry-summary")
    # The summary's only download is the packet: its counts export and the
    # data-load Technical details were removed (#1126).
    assert page.get_by_role("button", name="Queue complete export").count() == 0
    assert page.get_by_text("Technical details").count() == 0
    # Nothing ticked means every authorized Ministry: no selection is sent.
    packet = page.locator("form", has_text="Ministries in the packet")
    assert packet.locator("input[name=ministries]:checked").count() == 0
    assert packet.locator("input[type=radio]").count() == 0
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
    page.goto(component_origin + "/ministry-summary")
    page.get_by_role("checkbox", name="Example <Ministry>").check()
    page.get_by_label("Also include resolved and withdrawn requests").check()
    page.get_by_label("Packet format").select_option("xlsx")
    # The packet asks for no time zone (#1127): the browser's goes unasked.
    assert page.get_by_label("Packet timezone").count() == 0
    zone = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    page.route(
        "**" + reverse("admin:ministry_packet"),
        lambda route: route.fulfill(body="Queued"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Queue follow-up packet").click()
    body = sent.value.post_data
    assert "selection=" not in body and body.count("ministries=") == 1
    assert "ministries=9" in body
    assert "history=yes" in body and "format=xlsx" in body
    assert "browser_timezone=" + quote(zone, safe="") in body
    assert "request_key=00000000-0000-0000-0000-000000000060" in body
    assert "?" not in sent.value.url
    # Let the routed answer finish loading before the next navigation,
    # which it would otherwise interrupt (#623).
    visible(page.get_by_text("Queued", exact=True))
    # While other campaign work holds new exports, the packet waits.
    page.goto(component_origin + "/ministry-gated")
    assert page.get_by_role("button", name="Queue follow-up packet").is_disabled()


def test_ministry_totals_draw_follow_up_progress_bars(page, component_origin):
    """The totals are two tiles, then follow-up progress bars (#720).

    The chart draws under the CSP with no script error and is named and
    focusable; each bar's axis label is its name over its value, and its
    tooltip and accessible name carry the same figures, so there is no
    Exact values table or visible caption. Each table row's Follow-up
    progress cell has its bar, hidden from assistive technology. At 320 and
    1280 pixels the page fits its window with no text drawn over other text.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/ministry-summary")
    totals = page.locator("#ministry-totals")
    expect(totals.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    assert totals.locator(".stat-tile").all_inner_texts() == [
        "Join requests\n51",
        "Leave requests\n0",
    ]
    view = totals.locator("[data-chart-view]")
    assert view.get_attribute("aria-label") == "Follow-up progress"
    assert view.get_attribute("tabindex") == "0"
    assert totals.locator("figcaption").bounding_box()["width"] <= 1
    ticks = totals.locator("svg .role-axis-label text:has(tspan)")
    assert [tick.locator("tspan").all_text_contents() for tick in ticks.all()] == [
        ["Resolved or closed", "11 out of 51 (21.6%)"],
        ["Unresolved", "40 out of 51 (78.4%)"],
    ]
    bar = totals.locator("svg g.mark-rect").nth(1).locator("path").nth(1)
    assert bar.get_attribute("aria-label") == "Unresolved: 40 out of 51 (78.4%)"
    bar.hover()
    tooltip = page.locator("#vg-tooltip-element")
    visible(tooltip)
    assert "78.4%" in tooltip.inner_text()
    page.mouse.move(0, 0)
    expect(tooltip).to_be_hidden()
    expect(totals.get_by_text("Exact values")).to_have_count(0)
    cell = page.locator("tbody td.progress-cell").first
    assert cell.inner_text().strip() == "11 out of 51 (21.6%)"
    progress = cell.locator("progress")
    assert (progress.get_attribute("value"), progress.get_attribute("max")) == (
        "11",
        "51",
    )
    assert progress.get_attribute("aria-hidden") == "true"
    for width in (320, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        eventually(page, FITTED)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.evaluate(COLLISIONS) == []
    assert_clean(page, errors)


def test_applied_search_redraws_the_totals_in_place(page, component_origin):
    """Apply filters swaps in the matching Ministries' totals and draws the
    fresh chart without a reload (#720)."""
    from playwright.sync_api import expect

    errors = watch(page)
    answer = page.request.get(component_origin + "/ministry-summary-searched").text()
    page.goto(component_origin + "/ministry-summary")
    totals = page.locator("#ministry-totals")
    expect(totals.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    page.evaluate("window.__ministryMarker = 1")
    page.route(
        "**" + reverse("admin:ministry_report"),
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    page.get_by_role("button", name="Apply filters").click()
    expect(totals.locator(".stat-value").nth(1)).to_have_text("9")
    expect(totals.locator("[data-chart][data-chart-state=rendered]")).to_have_count(1)
    assert page.evaluate("window.__ministryMarker") == 1
    ticks = totals.locator("svg .role-axis-label text:has(tspan)")
    assert ticks.first.locator("tspan").all_text_contents() == [
        "Resolved or closed",
        "20 out of 60 (33.3%)",
    ]
    assert_clean(page, errors)
