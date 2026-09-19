"""Actual native delivery controls remain usable on narrow screens and keyboards."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_pause_resume_and_resolution_accessibility(
    page, component_origin, axe_source, width
):
    """Check real template semantics and layout, not browser/tool functionality."""
    page.set_viewport_size({"width": width, "height": 900})
    for action, label in (
        ("pause", "Reason for pausing live delivery"),
        ("resume", "Reason for resuming live delivery"),
        ("resolve", "Reason"),
    ):
        page.goto(component_origin + f"/delivery-{action}")
        reason = page.get_by_label(label, exact=True)
        reason.fill("")
        form = page.locator("form").filter(has=reason)
        assert not form.evaluate("element => element.checkValidity()")
        reason.focus()
        page.keyboard.type("Review held messages")
        assert form.evaluate("element => element.checkValidity()")
        assert "1,234" in page.locator("main").inner_text()
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
    page.get_by_label("Submission receipts", exact=False).check()
    page.get_by_label("Resolution", exact=True).select_option("cancel")
    assert page.get_by_role(
        "button", name="Confirm held-message resolution"
    ).is_visible()


def test_closed_resolution_without_javascript(browser_engine, component_origin):
    """A closed-campaign decision needs only ordinary labeled POST controls."""
    context = browser_engine.new_context(java_script_enabled=False)
    try:
        page = context.new_page()
        page.goto(component_origin + "/delivery-resolve")
        page.get_by_label("Weekly Admin reports", exact=False).check()
        page.get_by_label("Resolution", exact=True).select_option("cancel")
        page.get_by_label("Reason", exact=True).fill("Cancel after staff review")
        assert page.get_by_role(
            "button", name="Preview held-message resolution"
        ).is_visible()
        assert page.locator('form input[name="preview"]').get_attribute("value")
    finally:
        context.close()
