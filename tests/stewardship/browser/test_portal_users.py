"""Read-only Administrator review of login rules, provenance and assignments."""

import pytest

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

PAGES = ("/portal-users", "/portal-users-confirmed", "/portal-users-minimal")


@pytest.mark.parametrize("width", [320, 1280])
def test_portal_users_mobile_keyboard_and_accessibility(
    page, component_origin, axe_source, width
):
    """Wide tables stay reachable and readable at phone width without scripts."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in PAGES:
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
    page.goto(component_origin + "/portal-users")
    # A deliberate denial is labelled, never left looking like an empty accident.
    blocked = page.get_by_role("row", name="blocked@example.org", exact=False)
    assert blocked.get_by_text("Explicit deny", exact=True).is_visible()
    # A Chairperson-only role is suspended until the parish source confirms it.
    chair = page.get_by_role("row", name="chair@example.org", exact=False)
    assert chair.get_by_text(
        "The Ministry leader role is suspended", exact=False
    ).count()
    assert chair.get_by_text("suspended", exact=False).count() >= 2
    # A domain rule needs a real hosted-domain claim, not a matching suffix.
    unused = page.get_by_role("row", name="unused.example", exact=False)
    assert unused.get_by_text("No sign-in has presented", exact=False).is_visible()
    leader = page.get_by_role("row", name="leader@workspace.example", exact=False)
    assert leader.get_by_text("replaces its domain rule", exact=False).is_visible()
    assert leader.get_by_text("This Google identity is disabled.", exact=True).count()
    stray = page.get_by_role("row", name="stray@elsewhere.example", exact=False)
    assert stray.get_by_text("No login rule gives this person", exact=False).count()
    helper = page.get_by_role("row", name="helper@workspace.example", exact=False)
    assert helper.get_by_text("No login rule gives", exact=False).count() == 0
    # Scrollable table regions are keyboard reachable and named by their heading.
    region = page.get_by_role("region", name="Exact-address rules")
    region.focus()
    assert page.evaluate("document.activeElement.getAttribute('role')") == "region"
    # This slice reviews; it offers nothing that could change a rule.
    assert page.locator(".table-scroll :is(form, button, input, select)").count() == 0

    page.goto(component_origin + "/portal-users-confirmed")
    chair = page.get_by_role("row", name="chair@example.org", exact=False)
    assert (
        chair.get_by_text("The Ministry leader role is suspended", exact=False).count()
        == 0
    )
    page.goto(component_origin + "/portal-users-minimal")
    assert page.get_by_text("No hosted-domain rules.", exact=True).is_visible()
    assert (
        page.get_by_role("region", name="Ministry assignments", exact=False).count()
        == 0
    )
