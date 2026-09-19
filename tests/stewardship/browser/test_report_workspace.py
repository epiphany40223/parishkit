"""Real report templates retain exact values, native forms and accessible controls."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_report_chart_scope_export_controls_and_accessibility(
    page, component_origin, axe_source, width
):
    """Inspect exact facts without changing scope, then validate native export forms."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/participation")
    assert page.get_by_role("status").filter(has_text="Updating").is_visible()
    slider = page.get_by_role("slider", name="Inspect campaign date")
    slider.focus()
    page.keyboard.press("Home")
    values = page.locator("[data-digest-values]").inner_text()
    assert "Historical as of day" in values and "$1,234.56" in values
    assert page.locator("tbody tr").count() == 3
    page.get_by_label("Export format", exact=True).select_option("xlsx")
    button = page.get_by_role("button", name="Generate export")
    button.focus()
    assert page.locator("input[name=request_key]").count() == 1
    assert (
        page.locator("input[name=browser_timezone]").input_value()
        == "America/Los_Angeles"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for path in ("/participation", "/report-export", "/report-export-busy"):
        page.goto(component_origin + path)
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
          runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id, impact}) => ({id, impact}))""")
            == []
        )


def test_report_and_exports_work_without_scripts(browser_engine, component_origin):
    """No JavaScript is required to choose scope, read exact values or export."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/participation")
        assert "$3,234.56" in page.locator("table").inner_text()
        assert page.get_by_role("button", name="Apply report options").is_visible()
        assert page.get_by_role("button", name="Generate export").is_visible()
        assert page.locator("[data-digest-controls]").is_hidden()
        page.goto(component_origin + "/report-export")
        assert page.get_by_role("button", name="Download export").is_visible()
    finally:
        context.close()


def test_default_timezone_is_resolved_once_but_explicit_utc_is_kept(
    page, component_origin
):
    """Start with a genuinely different server fallback and retain selected scope."""
    from playwright.sync_api import expect

    page.goto(component_origin + "/participation-auto?scope=historical")
    expect(page).to_have_url(
        component_origin
        + "/participation-auto?scope=historical&timezone=America%2FLos_Angeles"
    )
    expect(page.locator("input[name=browser_timezone]")).to_have_value(
        "America/Los_Angeles"
    )
    page.goto(component_origin + "/participation-auto?timezone=UTC")
    expect(page.locator("select[name=timezone]")).to_have_value("UTC")
    assert page.url.endswith("?timezone=UTC")
