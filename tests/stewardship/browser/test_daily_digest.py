"""Pinned chart values are responsive, accessible and usable without JavaScript."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_chart_pointer_and_keyboard_preserve_exact_values(
    page, component_origin, axe_source, width
):
    """Input changes inspect retained data only and work under the actual CSP."""
    page.set_viewport_size({"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(component_origin + "/daily-digest")
    image = page.locator(".digest-chart")
    assert image.evaluate("image => image.complete && image.naturalWidth === 1440")
    slider = page.get_by_role("slider", name="Inspect campaign date")
    slider.focus()
    page.keyboard.press("Home")
    values = page.locator("[data-digest-values]")
    assert "2026-10-31" in values.inner_text()
    assert "1 out of 1,234 (0.1%)" in values.inner_text()
    assert "$1,234.56" in values.inner_text()
    assert slider.get_attribute("aria-valuetext") == values.inner_text()
    image.scroll_into_view_if_needed()
    bounds = image.bounding_box()
    image.hover(position={"x": bounds["width"] * 0.80, "y": bounds["height"] * 0.45})
    assert "2026-11-02" in values.inner_text()
    assert "$3,234.56" in image.get_attribute("title")
    assert not errors
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.evaluate(axe_source)
    violations = page.evaluate("""async () => (await axe.run(document, {
      runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(({id, impact}) => ({id, impact}))""")
    assert violations == []


def test_snapshot_retains_all_values_without_scripts(browser_engine, component_origin):
    """Static image, native download link and complete table remain useful."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/daily-digest")
        assert page.locator("tbody tr").count() == 3
        assert "$3,234.56" in page.locator("table").inner_text()
        assert page.get_by_role(
            "link", name="Download the original chart (PNG)"
        ).is_visible()
        assert page.locator("[data-digest-controls]").is_hidden()
    finally:
        context.close()
