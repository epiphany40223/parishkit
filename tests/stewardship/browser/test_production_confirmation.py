"""Production controls use native accessible forms on mobile and desktop."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_confirmation_and_progress_are_accessible(
    page, component_origin, axe_source, width
):
    """Exercise actual templates, keyboard input, responsive layout and WCAG."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/production-confirmation")
    field = page.get_by_label("Type Production to confirm this exact transition")
    form = page.locator("form").filter(has=field)
    assert not form.evaluate("element => element.checkValidity()")
    field.fill("production")
    assert not form.evaluate("element => element.checkValidity()")
    field.fill("")
    field.focus()
    page.keyboard.type("Production")
    assert form.evaluate("element => element.checkValidity()")
    assert "1,000 out of 1,500 (66.7%)" in page.locator("main").inner_text()

    # Enter submits this native form in every engine, independently of the
    # host OS setting controlling whether Tab visits non-text controls.
    def receive(route):
        """A local success response avoids testing browser-native 405 error pages."""
        if route.request.method == "POST":
            assert "typed=Production" in route.request.post_data
            assert "preview=synthetic-confirmation" in route.request.post_data
            route.fulfill(status=200, content_type="text/plain", body="Received")
        else:
            route.continue_()

    page.route("**/production-confirmation", receive)
    with page.expect_navigation(wait_until="load") as submitted:
        page.keyboard.press("Enter")
    assert submitted.value.status == 200
    for path in ("/production-confirmation", "/production-progress"):
        page.goto(component_origin + path)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        )
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    assert page.get_by_role("heading", name="Campaign active", exact=True).is_visible()
    assert page.get_by_role(
        "heading", name="Preparing initial campaign mail"
    ).is_visible()
    assert "5,678" in page.locator("main").inner_text()
    assert "4,800" in page.locator("main").inner_text()
    assert "-200" in page.locator("main").inner_text()
    assert (
        "Differences so far are not final reductions"
        in page.locator("main").inner_text()
    )


def test_production_forms_do_not_require_javascript(browser_engine, component_origin):
    """Confirmation and recovery remain ordinary form submissions without scripts."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/production-confirmation")
        page.get_by_label("Type Production to confirm this exact transition").fill(
            "Production"
        )
        assert page.get_by_role(
            "button", name="Confirm Production", exact=True
        ).is_visible()
        assert page.locator('form input[name="preview"]').get_attribute("value")
        page.goto(component_origin + "/production-progress")
        assert page.get_by_role(
            "button", name="Retry failed mail preparation"
        ).is_visible()
        assert page.get_by_role("link", name="Refresh Production progress").is_visible()
        assert page.locator('form input[name="control"]').get_attribute("value")
    finally:
        context.close()
