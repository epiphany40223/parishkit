"""Private weekly report pages remain usable on phones and without JavaScript."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_weekly_detail_is_responsive_accessible_and_escaped(
    page, component_origin, axe_source, width
):
    """Native links and historical status remain understandable at both sizes."""
    page.set_viewport_size({"width": width, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(component_origin + "/weekly-digest")
    page.get_by_role(
        "link", name="Open full captured request and current status"
    ).click()
    assert page.get_by_role("heading", name="Full text at capture").is_visible()
    assert "1,234,567" in page.locator("main").inner_text()
    assert "Superseded by a later response" in page.locator("main").inner_text()
    assert (
        "<script>not executable</script>"
        in page.locator(".content-sample").inner_text()
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not errors
    page.evaluate(axe_source)
    violations = page.evaluate("""async () => (await axe.run(document, {
      runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']}
    })).violations.map(({id, impact}) => ({id, impact}))""")
    assert violations == []


def test_weekly_report_remains_usable_without_scripts(browser_engine, component_origin):
    """Reading full requests and paging require no JavaScript enhancement."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/weekly-digest")
        assert page.get_by_role("link", name="Next page").is_visible()
        page.get_by_role(
            "link", name="Open full captured request and current status"
        ).click()
        assert page.get_by_role("heading", name="Full text at capture").is_visible()
        assert page.get_by_role(
            "link", name="Return to this weekly report"
        ).is_visible()
    finally:
        context.close()
