"""Native private financial stewardship detail report and its filters."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

PAGES = (
    "/financial-report",
    "/financial-unproven",
    "/financial-empty",
    "/financial-last",
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
    assert page.get_by_text("contributions through 2026-06-30", exact=False).count()
    # The share filter offers escaped labels and keeps the current choice.
    assert page.get_by_label("Share method").input_value() == "online"
    assert page.get_by_label("At least").input_value() == "100.00"
    search = page.get_by_label("Search Family name or Family DUID")
    search.focus()
    page.keyboard.press("Tab")
    assert page.locator(":focus").get_attribute("name") == "active"
    assert page.get_by_role("button", name="Next page").count() == 1
    assert page.get_by_role("button", name="Previous page").count() == 0
    page.goto(component_origin + "/financial-last")
    assert page.get_by_role("button", name="Next page").count() == 0
    assert page.get_by_role("button", name="Previous page").count() == 1
    # Unproven giving is explained and shown as unavailable, never as zero.
    page.goto(component_origin + "/financial-unproven")
    assert page.get_by_text("Unavailable does not mean zero", exact=False).is_visible()
    assert page.get_by_role("cell", name="Unavailable", exact=True).count() == 2
    assert page.get_by_role("cell", name="$0.00", exact=True).count() == 1
    assert page.get_by_text("Status unavailable", exact=False).count() >= 1
    assert page.get_by_text("None chosen", exact=True).count() == 1
    page.goto(component_origin + "/financial-empty")
    assert page.get_by_text("No matching pledges.", exact=True).is_visible()


def test_financial_filters_and_pages_without_scripts(browser_engine, component_origin):
    """Identifying filters and page changes post natively, never through the URL."""
    context = browser_engine.new_context(java_script_enabled=False)
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
        assert "frequency=monthly" in body and "share=online" in body
        assert "Private" not in sent.value.url and "?" not in sent.value.url

        # Pagination carries the applied filters, not the edited form fields.
        page.goto(component_origin + "/financial-report")
        with page.expect_request(lambda request: request.method == "POST") as sent:
            page.get_by_role("button", name="Next page").click()
        body = sent.value.post_data
        assert "page=2" in body and "search=Example" in body
        assert "pledge_min=100.00" in body and "?" not in sent.value.url
    finally:
        context.close()
