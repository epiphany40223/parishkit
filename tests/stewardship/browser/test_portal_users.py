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
    # Google verified that person's attempt; being refused is not a sign-in.
    assert blocked.get_by_text("None on record", exact=True).count() == 1
    assert blocked.locator("time").count() == 0
    # A Chairperson-only role is suspended until the parish source confirms it.
    chair = page.get_by_role("row", name="chair@example.org", exact=False)
    assert chair.get_by_text(
        "The Ministry leader role is suspended", exact=False
    ).count()
    assert chair.get_by_text("suspended", exact=False).count() >= 2
    # A domain rule needs a real hosted-domain claim, not a matching suffix.
    unused = page.get_by_role("row", name="unused.example", exact=False)
    assert unused.get_by_text(
        "No recorded Google account is authorized", exact=False
    ).count()
    # The used domain counts only the helper: the leader has an exact rule and a
    # disabled identity, and the consumer account presented no hosted claim.
    used = page.get_by_role("row", name="workspace.example Staff", exact=False).first
    assert used.get_by_role("cell", name="1", exact=True).count() == 1
    leader = page.get_by_role("row", name="leader@workspace.example", exact=False)
    assert leader.get_by_text("replaces its domain rule", exact=False).is_visible()
    # Policy still grants the address; the warning says this identity cannot use it.
    assert leader.get_by_text("is disabled and cannot sign in", exact=False).count()
    assert leader.get_by_role("cell", name="Staff, Ministry leader", exact=True).count()
    stray = page.get_by_role("row", name="stray@elsewhere.example", exact=False)
    assert stray.get_by_text("No login rule gives this person", exact=False).count()
    # Judged by the hosted-domain claim really presented, never the email ending.
    helper = page.get_by_role("row", name="helper@workspace.example", exact=False)
    assert helper.get_by_role("cell", name="Yes", exact=True).count() == 1
    consumer = page.get_by_role("row", name="consumer@workspace.example", exact=False)
    assert consumer.get_by_role("cell", name="No", exact=True).count() == 1
    assert consumer.get_by_text("No usable Google identity", exact=False).count()
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
