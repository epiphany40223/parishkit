"""The dashboard's security event panel across engines, keyboards and widths."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


@pytest.mark.parametrize("width", [320, 1280])
def test_security_events_are_prominent_accessible_and_acknowledgeable(
    page, component_origin, axe_source, width
):
    """Every open expansion is named, worded and acknowledgeable without scripts."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in ("/dashboard-events", "/dashboard-clear"):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    page.goto(component_origin + "/dashboard-events")
    panel = page.get_by_role("region", name="Security events awaiting acknowledgement")
    assert panel.count() == 1
    assert panel.get_by_role("listitem").count() == 3
    # Each expansion is named by the specification's wording, with its roles.
    assert panel.get_by_text("Administrator added to an exact address").count() == 1
    assert panel.get_by_text("Hosted-domain rule created").count() == 1
    assert panel.get_by_text("Staff added to a hosted-domain rule").count() == 1
    assert (
        panel.get_by_text("Roles before: none. Roles after: Administrator.").count()
        == 1
    )
    assert (
        panel.get_by_text(
            "Roles before: Staff, Ministry leader. Roles after: Staff, Ministry leader."
        ).count()
        == 0
    )
    # A target is shown as text, never interpreted as markup.
    assert panel.get_by_text("<b>partner.example</b>").count() == 1
    assert panel.locator("b").count() == 0
    buttons = panel.get_by_role("button", name="Acknowledge")
    assert buttons.count() == 3
    # Each button is a native form posting to its own event's route.
    forms = panel.locator("form")
    assert forms.count() == 3
    assert forms.nth(0).get_attribute("method").lower() == "post"
    assert (
        forms.nth(0)
        .get_attribute("action")
        .endswith("/security-events/00000000-0000-0000-0000-000000000001/acknowledge")
    )
    assert forms.nth(0).locator("input[name=csrfmiddlewaretoken]").count() == 1
    buttons.first.focus()
    assert page.locator(":focus").inner_text() == "Acknowledge"
    page.goto(component_origin + "/dashboard-clear")
    assert (
        page.get_by_role(
            "region", name="Security events awaiting acknowledgement"
        ).count()
        == 0
    )
