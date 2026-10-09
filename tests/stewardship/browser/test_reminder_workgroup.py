"""The Reminder WorkGroup setting page (#861) in a real browser."""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
PATHS = (
    "/reminder-workgroup-found",
    "/reminder-workgroup-missing",
    "/reminder-workgroup-off",
    "/reminder-workgroup-preview",
)


@pytest.mark.parametrize("width", [320, 1280])
def test_reminder_workgroup_pages_are_accessible_and_fit(
    page, component_origin, axe_source, width
):
    """Every state and the review pass axe and fit a phone without scrolling."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in PATHS:
        failures = []
        page.on("pageerror", lambda error, seen=failures: seen.append(str(error)))
        page.goto(component_origin + path)
        assert not failures
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )


def test_the_field_explains_itself_and_each_state_says_what_happens(
    page, component_origin
):
    """The name field has its plain-language help; each state is stated."""
    page.goto(component_origin + "/reminder-workgroup-found")
    field = page.get_by_label("ParishSoft Family WorkGroup")
    assert field.input_value() == "Active: Stewardship 2027"
    visible(page.get_by_text("Families in that WorkGroup get no Reminder emails"))
    visible(page.get_by_text("found 2 Families in"))
    page.goto(component_origin + "/reminder-workgroup-missing")
    visible(page.locator("[data-workgroup-status=missing]"))
    page.goto(component_origin + "/reminder-workgroup-off")
    assert page.get_by_label("ParishSoft Family WorkGroup").input_value() == ""
    visible(page.locator("[data-workgroup-status=off]"))
    page.goto(component_origin + "/reminder-workgroup-preview")
    visible(page.get_by_role("button", name="Apply changes"))
