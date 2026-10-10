"""Native private financial stewardship detail report and its filters."""

import pytest
from django.urls import reverse

from .waits import visible

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
    page.goto(component_origin + "/financial-report")
    assert page.get_by_text("Example <Family>", exact=False).count() == 1
    assert page.get_by_text("Stock <gift>", exact=False).count() == 1
    assert page.get_by_role("cell", name="$1,234.50", exact=True).count() == 1
    assert page.get_by_text("$102.88", exact=False).count() == 1
    assert page.locator("[data-summary=annual-total]").inner_text() == "$62,959.50"
    assert page.get_by_text("contributions through June 30, 2026", exact=False).count()
    # The share filter offers escaped labels and keeps the current choice.
    assert page.get_by_label("Share method").input_value() == ONLINE
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
    # Family, Annual pledge and Latest response sort through the SQL selection.
    assert page.locator("th[aria-sort=ascending]").inner_text().startswith("Family")
    assert page.locator("th button.sort-link").count() == 3
    # The complete export is offered beside the page, with its own controls.
    assert page.get_by_role("button", name="Queue complete export").count() == 1
    assert page.get_by_label("Export format").input_value() == "csv"
    assert page.get_by_label("Export timezone").count() == 1
    # While the campaign cannot accept work every export control is disabled.
    page.goto(component_origin + "/financial-gated")
    assert page.get_by_role("button", name="Queue complete export").is_disabled()
    assert page.get_by_label("Export format").is_disabled()
    assert page.get_by_label("Export timezone").is_disabled()
    page.goto(component_origin + "/financial-last")
    assert page.get_by_role("button", name="Next", exact=True).count() == 0
    assert page.get_by_role("button", name="Previous", exact=True).count() == 2
    # Unproven giving is explained and shown as unavailable, never as zero.
    page.goto(component_origin + "/financial-unproven")
    visible(page.get_by_text("Unavailable does not mean zero", exact=False))
    assert page.get_by_role("cell", name="Unavailable", exact=True).count() == 2
    assert page.get_by_role("cell", name="$0.00", exact=True).count() == 1
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
    page.get_by_label("Frequency").select_option("monthly")
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

    # The export carries the applied filters, the chosen format and the
    # one-time key. The fixture does not offer the browser's zone
    # (America/Los_Angeles), so the export timezone stays UTC.
    page.goto(component_origin + "/financial-report")
    page.get_by_label("Export format").select_option("pdf")
    page.route(
        "**" + reverse("admin:financial_export"),
        lambda route: route.fulfill(body="Queued"),
    )
    with page.expect_request(lambda request: request.method == "POST") as sent:
        page.get_by_role("button", name="Queue complete export").click()
    body = sent.value.post_data
    assert "format=pdf" in body and "browser_timezone=UTC" in body
    assert "search=Example" in body and "request_key=" in body
    assert "page=" not in body and sent.value.url.endswith(
        reverse("admin:financial_export")
    )


def test_financial_columns_lead_with_family_and_name_the_parishsoft_year(
    page, component_origin
):
    """The downloads' columns in their order; the ParishSoft year has a tip.

    Family first, then Latest and First response last, with no status,
    version or reference column (#932, decisions B1 and E1).
    """
    from parishkit.stewardship.reports.financial_documents import headings

    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/financial-report")
    shown = page.locator("table thead th").all_inner_texts()
    expected = headings("2025-07-01", "2026-06-30")
    # A sortable heading or a tip's "i" button adds text after the name.
    assert len(shown) == len(expected)
    assert all(
        text.startswith(name) for text, name in zip(shown, expected, strict=True)
    )
    # Latest and First response are the last two cells, one time each.
    row = page.locator("table tbody tr").first
    assert row.locator("td").last.locator("time").count() == 1
    assert row.locator("td").nth(-2).locator("time").count() == 1
    assert "Version" not in row.inner_text()


# The table's scroll box, in page coordinates, with its scroll area: none of
# it may change when a heading's tip opens.
TABLE_BOX = """() => {
    const box = document.querySelector(".table-scroll");
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
    rows = page.locator("table tbody tr").count()
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
    assert page.evaluate(TABLE_BOX) == before
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
    page.wait_for_function(
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
    const box = document.querySelector(".table-scroll");
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
    page.evaluate("document.querySelector('.table-scroll').style.width = '120px'")
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
