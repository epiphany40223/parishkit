"""Ministry requests (#1133): the review worklist (#1684) with its By Ministry
counts and Follow-up packet, across engines and widths."""

from urllib.parse import quote

import pytest
from django.urls import reverse

from .followup_components import (
    COUNTED,
    GATED_REQUESTS,
    HUB,
    LEADER,
    PACKET_LIMIT,
    PACKET_REFUSED,
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


def test_by_ministry_marks_and_one_line_for_one_ministry(page, component_origin):
    """Staff see a By Ministry table with each Ministry's ParishSoft and
    campaign marks; a leader of one Ministry sees one line with no marks
    (#1133, #1135). A zero count is plain text."""
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
    open_by_ministry(page)
    section = page.locator("#by-ministry")
    assert section.locator("table").count() == 0
    line = section.locator(".by-ministry-line")
    assert " ".join(line.inner_text().split()) == (
        "Example <Ministry>: 51 join requests · 0 leave requests · 40 to review"
    )
    visible(line.get_by_role("link", name=JOINS))
    # Neither the marks nor the help about them (the queue's own rows keep
    # their Removed badge).
    text = section.text_content() + page.locator(".about-page").text_content()
    for hidden in ("Not in ParishSoft", "Removed from this campaign"):
        assert hidden not in text


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
    section = page.locator("#by-ministry")
    assert section.locator(".leader-status").count() == 0
    assert "Leaders" not in section.inner_text()
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


def packet_button(page, name="Export PDF"):
    """One of the packet's export buttons (#1679), apart from the queue's own
    Export these requests row on the same page."""
    return page.get_by_role("group", name="Export the follow-up packet").get_by_role(
        "button", name=name
    )


def test_packet_request_posts_its_selection(page, component_origin):
    """The packet posts its chosen Ministries; nothing ticked means every
    Ministry in scope, and it waits while other campaign work runs."""
    page.goto(component_origin + REQUESTS)
    packet = page.locator("form", has_text="Ministries in the packet")
    assert packet.locator("input[name=ministries]:checked").count() == 0
    page.route(
        "**" + reverse("admin:ministry_packet"),
        lambda route: route.fulfill(
            body="file",
            headers={"Content-Disposition": 'attachment; filename="packet.pdf"'},
        ),
    )
    with (
        page.expect_request(lambda request: request.method == "POST") as sent,
        page.expect_download() as download,
    ):
        packet_button(page).click()
    assert "ministries=" not in sent.value.post_data
    assert "format=pdf" in sent.value.post_data
    # The file downloads in place (#1778), before the next navigation.
    assert download.value.suggested_filename == "packet.pdf"
    page.goto(component_origin + REQUESTS)
    page.get_by_role("checkbox", name="Example <Ministry>", exact=True).check()
    page.get_by_label("Also include resolved and withdrawn requests").check()
    zone = page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
    page.route(
        "**" + reverse("admin:ministry_packet"),
        lambda route: route.fulfill(
            body="file",
            headers={"Content-Disposition": 'attachment; filename="packet.xlsx"'},
        ),
    )
    with (
        page.expect_request(lambda request: request.method == "POST") as sent,
        page.expect_download() as download,
    ):
        packet_button(page, "Export XLSX").click()
    body = sent.value.post_data
    assert body.count("ministries=") == 1 and "ministries=9" in body
    assert "history=yes" in body and "format=xlsx" in body
    assert "browser_timezone=" + quote(zone, safe="") in body
    assert "?" not in sent.value.url
    # The packet downloads in place (#1778): the file arrives on this page.
    assert download.value.suggested_filename == "packet.xlsx"
    page.goto(component_origin + GATED_REQUESTS)
    assert packet_button(page, "Export XLSX").is_disabled()


def test_packet_waits_while_too_many_ministries_are_ticked(page, component_origin):
    """Past the packet limit (#1332), Queue waits with the count as its
    reason, in a reserved line that moves nothing; unticking one, or every
    one, brings Queue back."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + PACKET_LIMIT)
    queue = packet_button(page)
    hint = page.locator("#ministry-packet-hint")
    boxes = page.locator("#ministry-packet-choices input[name=ministries]")
    assert boxes.count() == 2 and queue.is_enabled()
    assert "ministry-packet-hint" in queue.get_attribute("aria-describedby").split()
    # Page coordinates: ticking a box scrolls it into view. Compared within
    # half a pixel, as same_place does: Linux Firefox adds float noise of
    # ~0.00005 px to an unmoved box; a real shift moves a whole pixel.
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
    assert queue.evaluate(where) == pytest.approx(place, abs=0.5)
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


# The packet's top and the row it reserves below Queue, in page
# coordinates: Queue's offset from the packet's heading, which the error
# summary above it moves along with everything else when it goes.
PACKET_PLACES = """() => {
  const top = (node) => node.getBoundingClientRect().top + scrollY;
  const heading = document.getElementById("ministry-packet-heading");
  const queue = heading.closest("section").querySelector("[type=submit]");
  const row = document.querySelector("#ministry-packet-hint").parentElement;
  return [top(queue) - top(heading), row.getBoundingClientRect().height];
}"""


def test_refused_packet_is_shown_at_its_boxes(page, component_origin, axe_source):
    """A packet the server refused for ticking too many (#1332) is drawn
    again with the offered ticks kept and every box marked, its message
    once in the reserved hint row, over the gate's hint; the first edit
    clears the marks, message and summary item, and moves nothing in the
    packet."""
    from playwright.sync_api import expect

    from parishkit.stewardship.reports.ministry_export_views import PACKET_MESSAGE

    errors = watch(page)
    page.goto(component_origin + PACKET_REFUSED)
    message = str(PACKET_MESSAGE)
    boxes = page.locator("#ministry-packet-choices input[name=ministries]")
    assert boxes.count() == 2
    for index in range(2):
        box = boxes.nth(index)
        expect(box).to_be_checked()
        expect(box).to_have_attribute("aria-invalid", "true")
        expect(box).to_have_attribute("aria-describedby", "ministry-packet_error")
    line = page.locator("#ministry-packet_error")
    expect(line).to_have_text(message)
    expect(line).to_be_visible()
    # The gate's own hint (two ticked, more than one) is held under it, so
    # the reason is shown once at the packet, and once in the summary.
    hint = page.locator("#ministry-packet-hint")
    assert hint.evaluate("node => getComputedStyle(node).visibility") == "hidden"
    packet = page.locator("#ministry-packet-heading").locator("xpath=..")
    assert packet.inner_text().count(message) == 1
    summary = page.locator("[data-error-summary]")
    expect(summary).to_be_focused()
    queue = packet_button(page)
    assert queue.is_disabled()
    # The value no box offers is never echoed, and no link or form carries
    # the selection.
    assert "77" not in packet.inner_html()
    assert page.evaluate("""() => [...document.querySelectorAll(
        "a[href], form[action]")].every((node) => !/ministries=/.test(
        node.getAttribute("href") || node.getAttribute("action")))""")
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    summary.get_by_role("link", name=message).click()
    expect(page.locator("#ministry-packet-12")).to_be_focused()
    before = page.evaluate(PACKET_PLACES)
    boxes.nth(0).uncheck()
    for index in range(2):
        expect(boxes.nth(index)).not_to_have_attribute("aria-invalid", "true")
        assert boxes.nth(index).get_attribute("aria-describedby") is None
    expect(line).to_be_hidden()
    expect(summary).to_have_count(0)
    assert queue.is_enabled() and hint.inner_text() == ""
    assert page.evaluate(PACKET_PLACES) == pytest.approx(before, abs=0.5)
    assert_clean(page, errors)


def test_unrefused_packet_hint_row_is_the_hint_alone(page, component_origin):
    """With no refusal the row is exactly the gate's hint line and its
    margins (#1332)."""
    page.goto(component_origin + PACKET_LIMIT)
    row, hint = page.evaluate("""() => {
      const hint = document.getElementById("ministry-packet-hint");
      const style = getComputedStyle(hint);
      return [
        hint.parentElement.getBoundingClientRect().height,
        hint.getBoundingClientRect().height
          + parseFloat(style.marginTop) + parseFloat(style.marginBottom),
      ];
    }""")
    assert row == pytest.approx(hint, abs=0.5) and hint > 0
    assert page.locator("#ministry-packet_error").is_hidden()


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
