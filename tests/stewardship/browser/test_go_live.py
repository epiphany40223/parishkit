"""Irreversible acknowledgement and cleanup progress on mobile and desktop."""

import pytest

from .waits import visible

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
    visible(
        page.get_by_role("button", name="Retry failed cleanup from its checkpoints")
    )
    visible(page.get_by_role("link", name="Refresh cleanup progress"))
    visible(
        page.get_by_role("button", name="Cancel cleanup without restoring deleted data")
    )
    page.goto(component_origin + "/go-live-links")
    assert "2,500 out of 5,000 (50%)" in page.locator("main").inner_text()
    visible(page.get_by_role("button", name="Retry failed preparation"))
    visible(page.get_by_role("button", name="Cancel and discard these inactive links"))
    visible(page.get_by_role("link", name="Refresh preparation progress"))


@pytest.mark.parametrize("width", [320, 1280])
def test_stale_sign_in_offers_the_step_up_instead_of_start(
    page, component_origin, axe_source, width
):
    """Start is unavailable until the sign-in is fresh (#547).

    The page offers "Confirm with Google", returning to readiness, in place
    of the acknowledgement and Start; LOCAL names its own sign-in, not Google.
    """
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + "/go-live-stale")
    main = page.locator("main")
    assert "confirm it's you with Google before starting cleanup" in (main.inner_text())
    visible(page.get_by_role("button", name="Confirm with Google"))
    assert page.get_by_role("button", name="Start Testing cleanup").count() == 0
    assert page.locator("input[data-acknowledgment]").count() == 0
    step_up = page.locator("[data-cleanup-step-up] form")
    assert step_up.get_attribute("action") == "/admin/login"
    assert step_up.locator('input[name="next"]').get_attribute("value") == (
        "/admin/campaign/go-live/"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
    })).violations.map(({id,impact}) => ({id,impact}))""")
        == []
    )
    page.goto(component_origin + "/go-live-stale-local")
    text = page.locator("[data-cleanup-step-up]").inner_text()
    assert "confirm your sign-in before starting cleanup" in text
    assert "Google" not in text
