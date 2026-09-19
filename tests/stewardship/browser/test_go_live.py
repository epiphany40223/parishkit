"""Irreversible acknowledgement and cleanup progress on mobile and desktop."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_go_live_acknowledgement_and_status_are_accessible(
    page, component_origin, axe_source, width
):
    """Actual templates expose explicit intent, local times and formatted progress."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/go-live")
    confirmation = page.get_by_role(
        "checkbox",
        name=(
            "I acknowledge that deleting the inventoried Testing data is irreversible."
        ),
    )
    form = page.locator("form").filter(has=confirmation)
    assert not form.evaluate("element => element.checkValidity()")
    confirmation.focus()
    page.keyboard.press("Space")
    assert confirmation.is_checked() and form.evaluate(
        "element => element.checkValidity()"
    )
    assert "1,000 out of 1,500" in page.locator("main").inner_text()
    for path in ("/go-live", "/go-live-links", "/go-live-cleanup"):
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
    assert "5,000 out of 12,000" in page.locator("main").inner_text()
    assert page.get_by_role(
        "button", name="Retry failed cleanup from its checkpoints"
    ).is_visible()


def test_cleanup_controls_work_without_javascript(browser_engine, component_origin):
    """Native POST forms and status refresh links do not depend on enhancement."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/go-live")
        assert page.get_by_role("checkbox").is_visible()
        assert page.get_by_role("button", name="Start Testing cleanup").is_visible()
        page.goto(component_origin + "/go-live-cleanup")
        assert page.get_by_role("link", name="Refresh cleanup progress").is_visible()
        assert page.get_by_role(
            "button", name="Cancel cleanup without restoring deleted data"
        ).is_visible()
        page.goto(component_origin + "/go-live-links")
        assert "2,500 out of 5,000 (50%)" in page.locator("main").inner_text()
        assert page.get_by_role("button", name="Retry failed preparation").is_visible()
        assert page.get_by_role(
            "button", name="Cancel and discard these inactive links"
        ).is_visible()
        assert page.get_by_role(
            "link", name="Refresh preparation progress"
        ).is_visible()
    finally:
        context.close()
