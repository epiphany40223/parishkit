"""Production activation and Cancel go-live: accessible on mobile and desktop.

Confirm Production itself is the Go live page's step, in its review dialog
(test_go_live_confirm.py, #1585 slice 4).
"""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_progress_and_cancellation_are_accessible(
    page, component_origin, axe_source, width
):
    """Exercise actual templates, responsive layout and WCAG."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (
        "/production-withdrawal",
        "/production-progress",
        "/production-scheduled",
    ):
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
    visible(page.get_by_role("link", name="Cancel go-live"))
    page.goto(component_origin + "/production-progress")
    assert page.get_by_role("link", name="Cancel go-live").count() == 0
    visible(page.get_by_role("heading", name="Campaign active", exact=True))
    visible(page.get_by_role("heading", name="Preparing initial campaign mail"))
    # The outcomes note links the page it names (navigation rule 6, #521).
    visible(page.locator("main").get_by_role("link", name="Sent mail", exact=True))
    assert "5,678" in page.locator("main").inner_text()
    assert "4,800" in page.locator("main").inner_text()
    assert "-200" in page.locator("main").inner_text()
    assert (
        "Differences so far are not final reductions"
        in page.locator("main").inner_text()
    )


def test_production_forms_carry_their_versions(page, component_origin):
    """Recovery and cancellation post the exact control and preview they show."""
    page.goto(component_origin + "/production-progress")
    visible(page.get_by_role("button", name="Retry failed mail preparation"))
    visible(page.get_by_role("link", name="Refresh Production progress"))
    assert page.locator('form input[name="control"]').get_attribute("value")
    page.goto(component_origin + "/production-withdrawal")
    page.get_by_label("Reason for cancelling go-live").fill("Correct campaign settings")
    page.get_by_label(
        "I understand that deleted Testing data cannot be restored."
    ).check()
    visible(page.get_by_role("button", name="Preview cancellation"))
    visible(page.get_by_role("button", name="Confirm cancelling go-live"))
