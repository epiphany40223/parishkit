"""Native private financial stewardship detail report and its filters."""

import pytest
from django.urls import reverse

from .conftest import no_script_context
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
    # Family, Annual pledge and Responses sort through the SQL selection.
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


def test_financial_filters_and_pages_without_scripts(browser_engine, component_origin):
    """Identifying filters and page changes post natively, never through the URL."""
    context = no_script_context(browser_engine)
    try:
        page = context.new_page()
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
        # one-time key natively; without scripts the timezone stays UTC.
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
    finally:
        context.close()
