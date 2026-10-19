"""Cleanup and link preparation progress on mobile and desktop.

Go live's Check step, where go-live starts, has its own tests
(test_go_live_check.py, #1585).
"""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_go_live_status_is_accessible(page, component_origin, axe_source, width):
    """Actual templates expose local times and formatted progress."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in ("/go-live-links", "/go-live-cleanup"):
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
