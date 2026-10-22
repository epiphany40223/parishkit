"""Native private financial stewardship detail report and its filters."""

import pytest
from django.urls import reverse

from .test_campaign_comparison import COLLISIONS
from .test_charts import assert_clean, watch
from .test_response_dashboard import AXE, FITTED
from .waits import eventually, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# The sample page's chosen share method: a stable option identity, not a label.
ONLINE = "00000000-0000-0000-0000-000000000062"

PAGES = (
    "/financial-report",
    "/financial-unproven",
    "/financial-empty",
    "/financial-gated",
    "/financial-last",
    "/financial-error-400",
    "/financial-error-503",
)


@pytest.mark.parametrize("width", [320, 1280])
def test_financial_mobile_keyboard_and_accessibility(
    page, component_origin, axe_source, width
):
    """Every state stays readable, escaped and operable without a JavaScript UI."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in PAGES:
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    # One row the Family submitted itself (no chip) and one Staff entered.
    page.goto(component_origin + "/financial-unproven")
    assert page.get_by_text("Entered by Staff", exact=True).count() == 1
    page.goto(component_origin + "/financial-report")
    assert page.get_by_text("Example <Family>", exact=False).count() == 1
    # Other text is in its method's tip (#1544), still escaped.
    assert page.get_by_text("Another way: Stock <gift>", exact=True).count() == 1
    assert page.get_by_role("cell", name="$1,234.50", exact=True).count() == 1
    # Entered by Staff (#794) is the Latest response cell's Staff chip
    # (#1544), not a column of its own.
    assert page.get_by_role("columnheader", name="Entered by Staff").count() == 0
    assert page.get_by_text("Entered by Staff", exact=True).count() == 1
    # The installment shows its unit short and its frequency in full to a
    # screen reader (#1544).
    assert page.get_by_role("cell", name="$102.88 monthly", exact=True).count() == 1
    assert (
        page.locator("[data-stat=annual-total] .stat-value").inner_text()
        == "$62,959.50"
    )
    assert page.get_by_text("contributions through June 30, 2026", exact=False).count()
    # The share filter offers escaped labels and keeps the current choice.
    assert page.get_by_label("Share method", exact=True).input_value() == ONLINE
    chosen = page.locator("#filter-share option:checked")
    assert chosen.inner_text() == "Online <giving>"
    assert page.get_by_label("At least").input_value() == "100.00"
    search = page.get_by_label("Search Family name or Family DUID")
    search.focus()
    page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("name") == "active"
    # The shared navigator (#203), above and below the table.
    assert page.get_by_role("button", name="Next", exact=True).count() == 2
    assert page.get_by_role("button", name="Previous", exact=True).count() == 0
    visible(page.get_by_text("Page 1 of 2", exact=True).first)
    # Family, Family DUID (#960), Annual pledge and Latest response sort
    # through the SQL selection.
    assert page.locator("th[aria-sort=ascending]").inner_text().startswith("Family")
    assert page.locator("th button.sort-link").count() == 4
    # The complete export is the row of export buttons below the table
    # (#1679), XLSX first, with no format menu and no zone question.
    row = page.get_by_role("group", name="Export complete results")
    assert row.locator(".export-buttons button").all_inner_texts() == [
        "Export XLSX",
        "Export CSV",
        "Export PDF",
    ]
    assert page.get_by_label("Export format").count() == 0
    assert page.get_by_label("Export timezone").count() == 0
    # While the campaign cannot accept work every export button is disabled.
    page.goto(component_origin + "/financial-gated")
    for name in ("Export XLSX", "Export CSV", "Export PDF"):
        assert page.get_by_role("button", name=name).is_disabled()
    page.goto(component_origin + "/financial-last")
    assert page.get_by_role("button", name="Next", exact=True).count() == 0
    assert page.get_by_role("button", name="Previous", exact=True).count() == 2
    # Unproven giving is explained and shown as unavailable, never as zero.
    page.goto(component_origin + "/financial-unproven")
    visible(page.get_by_text("Unavailable does not mean zero", exact=False))
    # Both ParishSoft sums in the one cell, each named (#1544).
    both = "Pledged Unavailable Contributed Unavailable"
    assert page.get_by_role("cell", name=both, exact=True).count() == 1
    assert page.get_by_role("cell", name="$0", exact=True).count() == 1
    assert page.get_by_text("Status unavailable", exact=False).count() >= 1
    assert page.get_by_text("None chosen", exact=True).count() == 1
    page.goto(component_origin + "/financial-empty")
    visible(page.get_by_text("No matching pledges.", exact=False))
    # A refused filter explains the money format and offers a way back.
    page.goto(component_origin + "/financial-error-400")
    assert page.get_by_role("alert").get_by_text("without commas", exact=False).count()
    assert page.get_by_role("link", name="Return to Financial stewardship").count()
    page.goto(component_origin + "/financial-error-503")
    assert page.get_by_role("alert").get_by_text("retry later", exact=False).count()


def test_financial_filters_and_pages_post_privately(page, component_origin):
    """Identifying filters and page changes post in the body, never the URL."""
    page.goto(component_origin + "/financial-report")
    page.get_by_label("Search Family name or Family DUID").fill("Private name")
    page.get_by_label("At most").fill("5000.00")
    page.get_by_label("Frequency", exact=True).select_option("monthly")
    page.route("**/financial/", lambda route: route.fulfill(body="Filtered"))
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    body = sent.value.post_data
    assert "search=Private+name" in body and "pledge_max=5000.00" in body
    assert "frequency=monthly" in body and f"share={ONLINE}" in body
    assert "Private" not in sent.value.url and "?" not in sent.value.url
    # Let the routed answer finish loading before the next navigation,
    # which it would otherwise interrupt (#623).
    visible(page.get_by_text("Filtered", exact=True))

    # Pagination carries the applied filters, not the edited form fields.
    page.goto(component_origin + "/financial-report")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Next", exact=True).first.click()
    body = sent.value.post_data
    assert "page=2" in body and "search=Example" in body
    assert "pledge_min=100.00" in body and "?" not in sent.value.url
    # Let the routed answer finish loading before the next navigation,
    # which it would otherwise interrupt (#623).
    visible(page.get_by_text("Filtered", exact=True))

    # A sortable heading posts the same filters with its sort token.
    page.goto(component_origin + "/financial-report")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Annual pledge", exact=False).click()
    body = sent.value.post_data
    assert "sort=pledge_desc" in body and "search=Example" in body
    assert "page=" not in body and "?" not in sent.value.url
    # Let the routed answer finish loading before the next navigation,
    # which it would otherwise interrupt (#623).
    visible(page.get_by_text("Filtered", exact=True))

    # The export carries the applied filters, the clicked format, the
    # browser's zone (America/Los_Angeles), unasked, and the one-time key.
    page.goto(component_origin + "/financial-report")
    page.route(
        "**" + reverse("admin:financial_export"),
        lambda route: route.fulfill(body="Queued"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Export PDF").click()
    body = sent.value.post_data
    assert "format=pdf" in body and "browser_timezone=America%2FLos_Angeles" in body
    assert "search=Example" in body and "request_key=" in body
    assert "page=" not in body and sent.value.url.endswith(
        reverse("admin:financial_export")
    )


def test_financial_columns_lead_with_family_and_name_the_parishsoft_year(
    page, component_origin
):
    """The page's shorter columns in order; the ParishSoft year has a tip.

    Family first, then Family DUID in its own column (ui-conventions), and
    Latest response last. The ParishSoft heading names Pledged, then
    Contributed, as its cells stack them. First response and Entered by
    Staff are left to
    the downloads, whose columns are unchanged (#1544); Latest response
    shows its day alone with the Staff chip.
    """
    from parishkit.stewardship.reports.financial_documents import headings

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/financial-report")
    head = page.locator("[data-table-region] table thead")
    assert head.locator("tr").count() == 1
    # A sortable heading or a tip's "i" button adds text after the name.
    shown = head.locator("th").all_inner_texts()
    expected = [
        "Family",
        "Family DUID",
        "Annual pledge",
        "Installment",
        "Share methods",
        "ParishSoft (2025–2026)",
        "Latest response",
    ]
    assert len(shown) == len(expected), shown
    assert all(
        text.startswith(name) for text, name in zip(shown, expected, strict=True)
    ), shown
    # Each line's own name, then its "i" tip. Read the name from its text
    # node, not innerText: the "i" can wrap under the name, and Linux WebKit
    # then drops the space between them ("Pledgedi") where other engines
    # keep it.
    lines = head.locator("th").nth(5).locator(".stack-line")
    names = lines.evaluate_all("ls => ls.map((l) => l.firstChild.textContent.trim())")
    assert names == ["Pledged", "Contributed"]
    tips = [
        line.get_by_role("button", name="More information", exact=True).count()
        for line in lines.all()
    ]
    assert tips == [1, 1]
    # The downloads keep First response and Entered by Staff (#1544).
    download = headings("2025-07-01", "2026-06-30")
    assert download[-2:] == ("First response", "Entered by Staff")
    row = page.locator("[data-table-region] table tbody tr").first
    latest = row.locator("td").last
    assert latest.locator("time").count() == 1
    # The day alone, compact, in the browser's zone: no clock time.
    assert latest.locator("time").inner_text() == "Sep 19, 2026"
    assert latest.get_by_text("Staff", exact=True).is_visible()
    assert "Version" not in row.inner_text()


# Whether any cell of the results table's body wraps to a second line: the
# client rectangles of each cell's content must all share one line (every
# rectangle starts above every other one's bottom). A cell that stacks two
# values (.stack-line, #1544) is checked one stacked line at a time. The
# Share methods cell is the one exception: its chips may wrap inside it
# (#1569), so it is checked chip by chip, each chip with its tip on one line.
# Returns the offending cells' text with each row's height and the number
# of lines its Share methods chips take.
WRAPPED = """() => {
    const table = document.querySelector("[data-table-region] table");
    const shares = [...table.tHead.rows[0].cells].findIndex(
        (th) => th.textContent.trim().startsWith("Share methods"));
    const rows = [...table.tBodies[0].rows];
    const wrapped = [];
    for (const row of rows) {
        for (const cell of row.children) {
          const chips = cell.cellIndex === shares;
          const lines = cell.querySelectorAll(
            chips ? ":scope > .nowrap" : ".stack-line");
          for (const unit of lines.length ? lines : [cell]) {
            const range = document.createRange();
            range.selectNodeContents(unit);
            // Not a 1px screen-reader span.
            const rects = [...range.getClientRects()].filter((r) => r.width > 1);
            const top = Math.max(...rects.map((r) => r.top));
            const bottom = Math.min(...rects.map((r) => r.bottom));
            if (rects.length && top >= bottom) wrapped.push(unit.innerText);
          }
        }
    }
    // A chip line is a distinct top among the cell's chips.
    const chipLines = (row) => {
        const chips = row.cells[shares].querySelectorAll(":scope > .nowrap");
        const tops = [...chips].map((c) => Math.round(c.getBoundingClientRect().top));
        return new Set(tops).size;
    };
    const height = (row) => row.getBoundingClientRect().height;
    return [wrapped, rows.map((row) => [height(row), chipLines(row)])];
}"""
# The results table's scroll box: its visible and full widths.
BOX_WIDTHS = """() => {
    const box = document.querySelector("[data-table-region] .table-scroll");
    return [box.clientWidth, box.scrollWidth];
}"""


@pytest.mark.parametrize("path", ["/financial-report", "/financial-typical"])
def test_rows_stay_one_line_and_scroll_in_their_box_on_a_phone(
    page, component_origin, path
):
    """No row wraps at laptop width, and a phone scrolls the table (#1544).

    At 1280 px every cell is on one line, except that the ParishSoft cell
    stacks Contributed under Pledged, one line each, so each row is two
    short lines tall, and that Share methods chips may wrap inside their
    cell (#1569), each chip on one line, making that row taller; a table
    wider than the space beside the Admin menu scrolls in its own box, never
    the page. At 320 px the rows keep that shape and the table scrolls in its
    box, a focusable region.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + path)
    wrapped, heights = page.evaluate(WRAPPED)
    assert wrapped == [], wrapped
    # The two stacked ParishSoft lines plus the cell padding (about 63 px),
    # well under three lines of text. A row grows past that only as its
    # chips wrap (#1569): a chip line with its "i" tip is about 30 px.
    assert heights, heights
    for height, lines in heights:
        assert height < max(72, 20 + 32 * lines), heights
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.set_viewport_size({"width": 320, "height": 900})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    wrapped, _ = page.evaluate(WRAPPED)
    assert wrapped == [], wrapped
    visible_width, full_width = page.evaluate(BOX_WIDTHS)
    assert full_width > visible_width
    # The box is a focusable region, so a keyboard can scroll it.
    box = page.locator("[data-table-region] .table-scroll")
    assert box.get_attribute("tabindex") == "0"
    assert box.get_attribute("role") == "region"


def test_share_methods_show_every_short_label_chip_with_other_text_tips(
    page, component_origin
):
    """Each share method is its own short-label chip; Other text is a tip (#1569).

    The chips are in the row's order, named by their short labels, with no
    "+N" chip. A method with Other text has an "i" toggletip beside its chip
    that shows the full label and the text, reached and opened by keyboard,
    and opening it never moves or resizes the table's box. Only the Share
    methods cell may wrap; money and date cells stay no-wrap.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/financial-report")
    row = page.locator("[data-table-region] table tbody tr").first
    cell = row.locator("td").nth(3)
    assert cell.locator(".status-badge").all_inner_texts() == ["Online", "Another way"]
    assert cell.evaluate("c => getComputedStyle(c).whiteSpace") == "normal"
    for index in (1, 4, 5):
        money_or_date = row.locator("td").nth(index)
        assert money_or_date.evaluate("c => getComputedStyle(c).whiteSpace") == "nowrap"
    button = cell.get_by_role("button", name="More information", exact=True)
    assert button.count() == 1
    bubble = cell.locator(".toggletip-bubble")
    assert bubble.is_hidden()
    before = page.evaluate(TABLE_BOX)
    button.focus()
    page.keyboard.press("Enter")
    visible(bubble)
    # The full label, the Family's words, still escaped.
    assert bubble.inner_text() == "Another way: Stock <gift>"
    # Within a hundredth of a pixel: Firefox can return the same position
    # with float noise (2070.61669921875 vs 2070.616668701172), while any
    # real move or resize is at least a whole pixel.
    assert page.evaluate(TABLE_BOX) == pytest.approx(before, abs=0.01)
    page.keyboard.press("Escape")
    assert bubble.is_hidden()
    # Three methods, each its own chip, the Other one with its tip.
    page.goto(component_origin + "/financial-typical")
    rows = page.locator("[data-table-region] table tbody tr")
    cell = rows.first.locator("td").nth(3)
    assert cell.locator(".status-badge").all_inner_texts() == [
        "Online",
        "Envelope",
        "Other",
    ]
    tips = cell.locator(".toggletip-bubble")
    assert tips.count() == 1
    assert (
        tips.first.evaluate("b => b.textContent") == "Other: Quarterly stock transfer"
    )
    # A Family with one method that has Other text: its chip and an "i" tip.
    cell = rows.nth(2).locator("td").nth(3)
    assert cell.locator(".status-badge").all_inner_texts() == ["Other"]
    button = cell.get_by_role("button", name="More information", exact=True)
    button.click()
    visible(cell.locator(".toggletip-bubble"))
    assert cell.locator(".toggletip-bubble").inner_text() == "Other: Payroll gift"


# The results table's scroll box (not a chart's exact-values table, #720),
# in page coordinates, with its scroll area: none of
# it may change when a heading's tip opens.
TABLE_BOX = """() => {
    const box = document.querySelector("[data-table-region] .table-scroll");
    const table = box.querySelector("table").getBoundingClientRect();
    const outer = box.getBoundingClientRect();
    return [outer.height, box.scrollWidth, box.scrollHeight, box.scrollTop,
        box.scrollLeft, table.left + scrollX, table.top + scrollY, table.height];
}"""


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("path", ["/financial-report", "/financial-unproven"])
@pytest.mark.parametrize("column", ["pledged", "contributed"])
def test_heading_tips_float_without_moving_the_table(
    page, component_origin, width, path, column
):
    """Opening a heading's tip never resizes or scrolls the table box (#932).

    One row (the report page) and two rows (the unproven page), at phone
    and laptop widths: the bubble floats over the page instead of growing
    the box's scroll area or being clipped by it, and stays on screen.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + path)
    rows = page.locator("[data-table-region] table tbody tr").count()
    assert rows == {"/financial-report": 1, "/financial-unproven": 2}[path]
    button = page.locator(f'button[aria-controls="financial-source-{column}-tip"]')
    # Described by its bubble alone, not by the whole heading cell.
    assert button.get_attribute("aria-describedby") == f"financial-source-{column}-tip"
    # Playwright scrolls the box to reach the button at 320 px; measure after.
    button.scroll_into_view_if_needed()
    before = page.evaluate(TABLE_BOX)
    button.click()
    bubble = page.locator(f"#financial-source-{column}-tip")
    visible(bubble)
    assert "July 1, 2025" in bubble.inner_text()
    # Within a hundredth of a pixel: Firefox can return the same position
    # with float noise (2070.61669921875 vs 2070.616668701172), while any
    # real move or resize is at least a whole pixel.
    assert page.evaluate(TABLE_BOX) == pytest.approx(before, abs=0.01)
    # The whole bubble is drawn inside the viewport, beside its button.
    inside = bubble.evaluate(
        """b => { const r = b.getBoundingClientRect();
        return r.left >= 0 && r.top >= 0 && r.right <= innerWidth
            && r.bottom <= innerHeight; }"""
    )
    assert inside
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    # The page scrolling keeps it with its button, still without moving the box.
    page.mouse.wheel(0, 40)
    eventually(
        page,
        """id => { const b = document.getElementById(id).getBoundingClientRect();
        const t = document.querySelector(`[aria-controls="${id}"]`)
            .getBoundingClientRect();
        return Math.abs(b.top - t.bottom - 6) < 1
            || Math.abs(t.top - 6 - b.bottom) < 1; }""",
        arg=f"financial-source-{column}-tip",
    )
    assert page.evaluate(TABLE_BOX)[:5] == before[:5]
    page.keyboard.press("Escape")
    assert bubble.is_hidden()


# Scroll the table box sideways until the given tip's button is out of its
# visible area; returns whether that worked.
SCROLL_BUTTON_OUT = """id => {
    const box = document.querySelector("[data-table-region] .table-scroll");
    const button = document.querySelector(`[aria-controls="${id}"]`);
    const out = () => {
        const a = button.getBoundingClientRect(), b = box.getBoundingClientRect();
        return a.right <= b.left || a.left >= b.right;
    };
    // The button's edges in the box's content coordinates.
    const a = button.getBoundingClientRect(), b = box.getBoundingClientRect();
    const left = a.left - b.left - box.clientLeft + box.scrollLeft;
    const right = left + a.width;
    // Past its right edge (it leaves on the left), else before its left
    // edge (it leaves on the right).
    box.scrollLeft = Math.ceil(right) + 1;
    if (!out()) box.scrollLeft = Math.max(0, Math.floor(left - box.clientWidth) - 1);
    return [out(), left, right, box.clientWidth, box.scrollWidth];
}"""


@pytest.mark.parametrize("column", ["pledged", "contributed"])
def test_heading_tip_closes_when_its_button_scrolls_out(page, component_origin, column):
    """A floating tip closes once the table box scrolls its button away (#932).

    Fixed to the viewport, the bubble would otherwise stay over the page,
    pointing at a column the box no longer shows.
    """
    page.set_viewport_size({"width": 320, "height": 900})
    page.goto(component_origin + "/financial-report")
    # A narrow box, so that each heading's button can scroll fully out of
    # it in every engine (WebKit's 320 px table otherwise always shows a
    # sliver of the last column).
    page.evaluate(
        "document.querySelector('[data-table-region] .table-scroll')"
        ".style.width = '120px'"
    )
    tip = f"financial-source-{column}-tip"
    button = page.locator(f'button[aria-controls="{tip}"]')
    button.scroll_into_view_if_needed()
    button.click()
    bubble = page.locator("#" + tip)
    visible(bubble)
    moved = page.evaluate(SCROLL_BUTTON_OUT, tip)
    assert moved[0], moved
    bubble.wait_for(state="hidden")
    assert button.get_attribute("aria-expanded") == "false"


# The value labels drawn after the summary charts' bars (#1266): for each,
# its text and the pixels between its right end and its chart's right edge.
# A label cut off by the chart's edge would have a negative slack.
VALUE_LABELS = """() => [...document.querySelectorAll(
  "#financial-summary [data-chart-view] svg")].flatMap((svg) => {
    const edge = svg.getBoundingClientRect().right;
    return [...svg.querySelectorAll(".role-mark text")].map((text) =>
      [text.textContent, edge - text.getBoundingClientRect().right]);
  })"""


def test_summary_charts_draw_with_tooltips_and_labelled_values(
    page, component_origin, axe_source
):
    """The summary is tiles, then two drawn charts with values on the bars.

    Both charts draw under the CSP with no script error (#720); each bar is
    labelled after its end with its count and share of the matching
    Families, and there is no Exact values table (#1266); a bar's tooltip
    has the same count and share; markup in a share method's label stays
    text. At 320 and 1280 pixels the page fits its window, and every label
    ends inside its chart with no text drawn over other text.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/financial-report")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(2)
    summary = page.locator("#financial-summary")
    assert summary.locator(".stat-label").all_inner_texts() == [
        "Families",
        "Total annual pledges",
        "Cannot contribute financially",
    ]
    assert summary.get_by_role("heading", level=3).all_inner_texts() == [
        "Pledges by frequency",
        "Share methods chosen",
    ]
    frequency = page.locator("[data-chart='pledge-frequency-spec']")
    assert frequency.locator("svg g.mark-rect path").count() == 5
    view = frequency.locator("[data-chart-view]")
    assert view.get_attribute("aria-label") == "Pledges by frequency"
    # The summary would repeat the drawn values, so it is not shown; it
    # stays the view's description for assistive technology.
    caption = frequency.locator("figcaption")
    assert view.get_attribute("aria-describedby") == caption.get_attribute("id")
    assert caption.inner_text().startswith("Pledges by frequency: Weekly")
    assert caption.bounding_box()["width"] <= 1
    frequency.locator("svg g.mark-rect path").nth(1).hover()
    tooltip = page.locator("#vg-tooltip-element")
    visible(tooltip)
    assert "Monthly" in tooltip.inner_text() and "5.9%" in tooltip.inner_text()
    # Pointer away: axe must not catch the tooltip while it fades out.
    page.mouse.move(0, 0)
    expect(tooltip).to_be_hidden()
    assert_clean(page, errors)
    # The share methods' labels are drawn as text, never parsed as markup.
    shares = page.locator("[data-chart='share-methods-spec']")
    assert "Online <giving>" in shares.locator("svg text").all_text_contents()
    # Each bar's count and share, drawn after it, in the bars' order.
    assert shares.locator("svg .role-mark text").all_text_contents() == [
        "4 (7.8%)",
        "40 (78.4%)",
        "7 (13.7%)",
    ]
    # The bar's accessible name says the same values.
    assert (
        shares.locator("svg g.mark-rect path").nth(1).get_attribute("aria-label")
        == "Online <giving>: 40 families, 78.4% compared with matching Families"
    )
    expect(summary.locator(".chart-table")).to_have_count(0)
    expect(summary.get_by_text("Exact values")).to_have_count(0)
    assert_clean(page, errors)
    for width in (320, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        eventually(page, FITTED)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.evaluate(COLLISIONS) == []
        labels = page.evaluate(VALUE_LABELS)
        assert len(labels) == 8 and min(slack for _, slack in labels) >= 0, labels
    page.evaluate(axe_source)
    assert page.evaluate(AXE) == []
    assert_clean(page, errors)


# The filter form's top in page coordinates.
FORM_TOP = (
    "document.getElementById('table-filters').getBoundingClientRect().top + scrollY"
)


def test_full_length_bar_labels_fit_beside_a_long_name_on_a_phone(
    page, component_origin
):
    """A long share-method name leaves a full-length bar's label room (#1266).

    The name takes much of a phone-width chart, so the value axis must keep
    the label's whole room after the longest bar rather than at most half
    the plot, and an axis tick near the stretched end must not be cut off.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/financial-long-labels")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(2)
    for width in (320, 375, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        eventually(page, FITTED)
        assert page.evaluate(COLLISIONS) == []
        labels = page.evaluate(VALUE_LABELS)
        assert "1,031 (100%)" in dict(labels), labels
        assert min(slack for _, slack in labels) >= 0, labels
    assert_clean(page, errors)


def test_applied_filters_redraw_the_summary_charts_in_place(page, component_origin):
    """Apply filters swaps in the new summary and draws its charts, no reload.

    The fresh charts hold their predecessors' space until drawn, so the
    filters below them end where they were (#736).
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.set_viewport_size({"width": 1280, "height": 900})
    answer = page.request.get(component_origin + "/financial-filtered").text()
    page.goto(component_origin + "/financial-report")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(2)
    eventually(page, FITTED)
    page.evaluate("window.__financialMarker = 1")
    form = page.evaluate(FORM_TOP)
    page.route(
        "**/financial/",
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    page.get_by_role("button", name="Apply filters").click()
    expect(page.locator("[data-stat=families] .stat-value")).to_have_text("4")
    expect(page.locator("[data-chart][data-chart-state=rendered]")).to_have_count(2)
    eventually(page, FITTED)
    assert page.evaluate("window.__financialMarker") == 1
    assert abs(page.evaluate(FORM_TOP) - form) < 2
    # The fresh chart's labels carry the filtered counts: 1 of 4 is 25%.
    frequency = page.locator("[data-chart='pledge-frequency-spec']")
    assert frequency.locator("svg .role-mark text").all_text_contents()[0] == (
        "1 (25%)"
    )
    assert_clean(page, errors)


def test_a_reload_after_apply_shows_the_same_view(page, component_origin):
    """Apply posts privately; the address bar then names the view (#1586).

    The answer's hidden data-page-address (only the closed choices: here
    Frequency, the sort and the rows per page) replaces the address, so a
    browser reload asks for that address by GET, never sending the POST
    again, and draws the same view, the filtered summary included.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    route = reverse("admin:financial_report")
    address = route + "?frequency=monthly&sort=pledge_desc&size=100"
    answer = page.request.get(component_origin + "/financial-filtered").text()
    page.goto(component_origin + route)
    expect(page.locator("[data-stat=families] .stat-value")).to_have_text("51")
    page.route(
        lambda url: url.endswith(route),
        lambda sent: (
            sent.fulfill(body=answer, content_type="text/html")
            if sent.request.method == "POST"
            else sent.fallback()
        ),
    )
    page.get_by_label("Search Family name or Family DUID").fill("Private name")
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Apply filters").click()
    assert "search=Private+name" in sent.value.post_data
    expect(page.locator("[data-stat=families] .stat-value")).to_have_text("4")
    expect(page).to_have_url(component_origin + address)
    assert "Private" not in page.url
    # The reload is the address's GET: no POST is sent again.
    methods = []
    page.on("request", lambda request: methods.append(request.method))
    with page.expect_navigation():
        page.reload()
    expect(page.locator("[data-stat=families] .stat-value")).to_have_text("4")
    expect(page).to_have_url(component_origin + address)
    assert "POST" not in methods and "GET" in methods
    assert_clean(page, errors)


AT_MOST = (
    "Enter At most in whole dollars or dollars and cents, no less than At "
    "least, or clear it."
)
# Apply's top in page coordinates.
APPLY_BOX = (
    "document.querySelector('#table-filters [type=submit]')"
    ".getBoundingClientRect().y + scrollY"
)


def assert_refused_at_at_most(page):
    """Both ends are marked and share one message; the summary links At most."""
    from playwright.sync_api import expect

    for end in ("#pledge-min", "#pledge-max"):
        field = page.locator(end)
        expect(field).to_have_attribute("aria-invalid", "true")
        assert "pledge-range_error" in field.get_attribute("aria-describedby").split()
    expect(page.locator("#pledge-range_error")).to_have_text(AT_MOST)
    expect(page.locator("#pledge-range_error")).to_be_visible()
    summary = page.locator("[data-error-summary]")
    expect(summary).to_have_count(1)
    expect(summary.get_by_role("link", name=AT_MOST)).to_have_attribute(
        "href", "#pledge-max"
    )
    # The Filters panel holding the fields is open.
    assert page.locator("[data-filter-panel]").evaluate("node => node.open")


def assert_cleared(page):
    """No mark, message or summary item is left on either end."""
    from playwright.sync_api import expect

    for end in ("#pledge-min", "#pledge-max"):
        expect(page.locator(end)).not_to_have_attribute("aria-invalid", "true")
        assert "pledge-range_error" not in (
            page.locator(end).get_attribute("aria-describedby") or ""
        )
    expect(page.locator("#pledge-range_error")).to_be_hidden()
    expect(page.locator("[data-error-summary]")).to_have_count(0)


@pytest.mark.parametrize("end", ["#pledge-max", "#pledge-min"])
def test_refused_filter_is_shown_at_its_field(page, component_origin, end):
    """Filters the server refused are drawn again with the error at At most.

    The fields keep the values sent, the summary takes focus and its link
    moves focus to the field, and an edit of either end of the range clears
    the mark from both (#1332).
    """
    from playwright.sync_api import expect

    errors = watch(page)
    page.goto(component_origin + "/financial-refused")
    assert_refused_at_at_most(page)
    expect(page.locator("[data-error-summary]")).to_be_focused()
    expect(page.locator("#pledge-min")).to_have_value("500")
    expect(page.locator("#pledge-max")).to_have_value("100")
    page.locator("[data-error-summary] a").click()
    expect(page.locator("#pledge-max")).to_be_focused()
    page.locator(end).fill("50" if end == "#pledge-min" else "600")
    assert_cleared(page)
    assert_clean(page, errors)


def test_fixing_the_other_end_and_applying_clears_the_mark(page, component_origin):
    """A successful in-place Apply clears the server marks it does not repeat.

    The reader fixes the range at At least, its other end (whose edit
    clears the shared mark at once), and Apply's answer marks nothing: no
    mark, message or summary is left (#1332).
    """
    from playwright.sync_api import expect

    errors = watch(page)
    answer = page.request.get(component_origin + "/financial-filtered").text()
    page.goto(component_origin + "/financial-refused")
    page.route(
        "**/financial/",
        lambda route: route.fulfill(body=answer, content_type="text/html"),
    )
    # Marks a later answer must clear, set as an earlier answer would have
    # left them had the edit not cleared them (a mark on a field the
    # reader did not touch).
    page.locator("#pledge-min").fill("50")
    page.evaluate("""() => {
        const field = document.getElementById("pledge-max");
        field.setAttribute("aria-invalid", "true");
        field.setAttribute("data-field-error", "");
        field.setAttribute("aria-describedby", "pledge-range_error");
        const line = document.getElementById("pledge-range_error");
        line.hidden = false;
        line.querySelector("li").textContent = "stale";
    }""")
    page.get_by_role("button", name="Apply filters").click()
    expect(page.locator("[data-stat=families] .stat-value")).to_have_text("4")
    assert_cleared(page)
    assert_clean(page, errors)


# Each refused range's page, its reserved line, the end the reader edits
# with a value still out of order, and the message the server and the
# gate's hint both say (#1469).
REFUSED_RANGES = {
    "/financial-refused": ("pledge-range", "#pledge-max", "200", AT_MOST),
    "/financial-refused-first": (
        "first-response-dates",
        "#first-end",
        "2026-10-31",
        "Enter a first response Through date no earlier than its From date, "
        "or clear it.",
    ),
    "/financial-refused-latest": (
        "latest-response-dates",
        "#latest-end",
        "2026-10-31",
        "Enter a latest response Through date no earlier than its From date, "
        "or clear it.",
    ),
}
# The offsets of Apply, every range's message line, the zone note and the
# hint row from the filter form's top, and the hint row's height (#1469).
# The error summary above the form comes and goes with the mark, so places
# are measured within the form.
FILTER_PLACES = """() => {
  const form = document.getElementById("table-filters");
  const top = (node) =>
    node.getBoundingClientRect().top - form.getBoundingClientRect().top;
  const hint = document.getElementById("financial-filter-hint");
  return [
    top(form.querySelector("[type=submit]")),
    ...[...form.querySelectorAll(".field-error-line")].map(top),
    top(document.getElementById("financial-zone-help")),
    top(hint),
    hint.getBoundingClientRect().height,
  ];
}"""


@pytest.mark.parametrize("path", list(REFUSED_RANGES))
def test_refused_range_says_it_once_and_hands_back_the_hint(
    page, component_origin, path
):
    """A refused range's message is said once, at its line (#1469).

    While the server's message shows, the gate's hint in the same words is
    held hidden (visibility only); an edit that leaves the range out of
    order clears the server's mark, and the gate's own hint shows the
    reason again, still once. Neither moves Apply, the lines, the zone note
    or the hint row.
    """
    from playwright.sync_api import expect

    line, end, value, message = REFUSED_RANGES[path]
    errors = watch(page)
    page.goto(component_origin + path)
    expect(page.locator(f"#{line}_error")).to_have_text(message)
    expect(page.locator(f"#{line}_error")).to_be_visible()
    hint = page.locator("#financial-filter-hint")
    assert hint.evaluate("node => getComputedStyle(node).visibility") == "hidden"
    assert page.locator("#table-filters").inner_text().count(message) == 1
    before = page.evaluate(FILTER_PLACES)
    page.locator(end).fill(value)
    page.locator(end).blur()
    expect(page.locator(f"#{line}_error")).to_be_hidden()
    expect(page.locator("[data-error-summary]")).to_have_count(0)
    expect(hint).to_have_text(message)
    expect(hint).to_be_visible()
    assert page.locator("#table-filters").inner_text().count(message) == 1
    assert page.get_by_role("button", name="Apply filters").is_disabled()
    assert page.evaluate(FILTER_PLACES) == pytest.approx(before, abs=0.5)
    assert_clean(page, errors)


def test_in_place_apply_refused_marks_the_kept_filter_form(page, component_origin):
    """A refused in-place Apply marks the fields on the form it kept (#1332).

    No reload and no shift: the form keeps what was typed, takes the marks
    and message from the 400 answer into its reserved line, so Apply stays
    under the pointer, and the summary in the results takes focus.
    """
    from playwright.sync_api import expect

    errors = watch(page)
    answer = page.request.get(component_origin + "/financial-refused").text()
    page.goto(component_origin + "/financial-report")
    page.evaluate("window.__financialMarker = 1")
    page.route(
        "**/financial/",
        lambda route: route.fulfill(status=400, body=answer, content_type="text/html"),
    )
    page.locator("#pledge-max").fill("500")
    before = page.evaluate(APPLY_BOX)
    page.evaluate("""() => {
        window.__focused = [];
        document.addEventListener("focusin", (event) => {
            window.__focused.push(event.target.id || event.target.tagName);
        });
    }""")
    page.get_by_role("button", name="Apply filters").click()
    assert_refused_at_at_most(page)
    summary = page.locator("[data-table-region] [data-error-summary]")
    expect(summary).to_be_focused()
    # Straight from Apply to the summary, never through the field.
    assert "pledge-max" not in page.evaluate("window.__focused")
    assert abs(page.evaluate(APPLY_BOX) - before) < 1
    assert page.evaluate("window.__financialMarker") == 1
    # What was typed stays; the answer's own values are not written over it.
    expect(page.locator("#pledge-max")).to_have_value("500")
    expect(page.locator("#pledge-min")).to_have_value("100.00")
    page.locator("#pledge-max").fill("600")
    assert_cleared(page)
    # The browser reports the answer's 400 itself; nothing else may appear.
    assert_clean(page, [error for error in errors if "status of 400" not in error])
