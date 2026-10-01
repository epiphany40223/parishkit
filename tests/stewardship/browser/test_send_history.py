"""Family email sends in a real browser (#432): accessible at every width."""

import pytest

from .send_history_components import PAGE

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_send_history_is_accessible_at_phone_and_desktop_widths(
    page, component_origin, axe_source, width
):
    """The page passes axe, scrolls only its table on a phone, and names links."""
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + PAGE)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.evaluate(axe_source)
    assert (
        page.evaluate("""async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
    })).violations.map(({id,impact}) => ({id,impact}))""")
        == []
    )
    table = page.get_by_role("table", name="Family email sends")
    assert table.get_by_role("row").count() == 4
    # Each count link says what it lists, not just a number.
    assert (
        page.get_by_role(
            "link", name="1,087 sent emails of Invitation, Production: show them"
        ).count()
        == 1
    )
    assert page.get_by_role("link", name="In progress: watch it live").count() == 1
    assert (
        page.get_by_role(
            "link", name="show 3 failed emails of Invitation, Production"
        ).count()
        == 1
    )
