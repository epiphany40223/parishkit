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


def test_missing_controls_keeps_static_report_usable(page, component_origin):
    """A partial template must not turn optional chart enhancement into an error."""

    def omit_controls(route):
        """Keep the real response and CSP, omitting only the enhancement marker."""
        response = route.fetch()
        route.fulfill(
            response=response,
            body=response.text().replace(
                "data-digest-controls", "data-omitted-controls"
            ),
        )

    page.route("**/daily-digest", omit_controls)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: (
            errors.append(message.text) if message.type == "error" else None
        ),
    )
    with page.expect_response("**/digest-v1.js") as script:
        page.goto(component_origin + "/daily-digest")
    assert script.value.status == 200
    assert not errors
    assert page.locator("[data-omitted-controls]").count() == 1
    assert page.locator("[data-omitted-controls]").is_hidden()
    assert page.locator("input[type=range]").get_attribute("aria-valuetext") is None
    assert page.locator("tbody tr").count() == 3
