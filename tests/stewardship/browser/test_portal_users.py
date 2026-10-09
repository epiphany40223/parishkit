"""Administrator review and reviewed edits of login rules and assignments."""

import pytest
from django.urls import reverse

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

PAGES = (
    "/portal-users",
    "/ministry-assignments",
    "/chairpersons",
    "/portal-users-confirmed",
    "/portal-users-minimal",
    "/portal-users-preview",
    "/portal-users-preview-deny",
    "/portal-users-preview-remove",
    "/portal-users-refused",
)


@pytest.mark.parametrize("width", [320, 1280])
def test_portal_users_mobile_keyboard_and_accessibility(
    page, component_origin, axe_source, width
):
    """Wide tables stay reachable, readable and accessible at phone width."""
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
    visible(blocked.get_by_text("Explicit deny", exact=True))
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
    visible(leader.get_by_text("replaces its domain rule", exact=False))
    # Policy still grants the address; the warning says this identity cannot use it.
    assert leader.get_by_text("is disabled and cannot sign in", exact=False).count()
    assert leader.get_by_role("cell", name="Staff, Ministry leader", exact=True).count()
    # Scrollable table regions are keyboard reachable and named by their heading.
    region = page.get_by_role("region", name="Exact-address rules")
    region.focus()
    assert page.evaluate("document.activeElement.getAttribute('role')") == "region"
    # Every rule row offers its own reviewed change; a domain can never be
    # ticked Administrator, and an address row shows its applied roles ticked.
    # With scripting the ticks autosave, so each row's review button is hidden
    # but still there for the native path; every removal stays reviewed.
    assert page.get_by_role("button", name="Review role change").count() == 0
    assert (
        page.get_by_role(
            "button", name="Review role change", include_hidden=True
        ).count()
        == 6
    )
    assert page.get_by_role("button", name="Review removal").count() == 6
    domain_admin = used.get_by_label("Administrator")
    assert domain_admin.is_disabled() and not domain_admin.is_checked()
    assert leader.get_by_label("Staff").is_checked()
    assert leader.get_by_label("Ministry leader").is_checked()
    assert not leader.get_by_label("Administrator").is_checked()
    assert page.get_by_label("Email address").count() == 1
    assert page.get_by_label("Hosted domain").count() == 1
    # Assignments live on Ministry assignments (NAV-15), which this page links.
    assert page.get_by_role("button", name="Review assignment removal").count() == 0
    visible(page.get_by_role("link", name="Ministry assignments", exact=True).first)

    # Ministry assignments: domain-authorized people, judged by the claim
    # really presented, never the email ending; each removal is reviewed.
    page.goto(component_origin + "/ministry-assignments")
    stray = page.get_by_role("row", name="stray@elsewhere.example", exact=False)
    assert stray.get_by_text("No login rule gives this person", exact=False).count()
    helper = page.get_by_role("row", name="helper@workspace.example", exact=False)
    assert helper.get_by_role("cell", name="Yes", exact=True).count() == 1
    consumer = page.get_by_role("row", name="consumer@workspace.example", exact=False)
    assert consumer.get_by_role("cell", name="No", exact=True).count() == 1
    assert consumer.get_by_text("No usable Google identity", exact=False).count()
    leader = page.get_by_role("row", name="leader@workspace.example", exact=False)
    assert leader.get_by_role("button", name="Review assignment removal").count()

    # The review page states before and after, the expansion and the reach.
    page.goto(component_origin + "/portal-users-preview")
    visible(page.get_by_text("High-impact expansion", exact=False))
    assert page.get_by_text(
        "Configured roles after this change: Administrator, Staff"
    ).count()
    assert page.get_by_text("2 usable recorded Google identities", exact=False).count()
    assert page.get_by_role("button", name="Apply login rule change").count() == 1
    assert page.get_by_role("link", name="Cancel").count() == 1
    page.goto(component_origin + "/portal-users-preview-deny")
    assert page.get_by_text("none: an explicit deny", exact=False).count()
    page.goto(component_origin + "/portal-users-preview-remove")
    visible(page.get_by_text("This rule will be removed.", exact=False))
    assert page.get_by_text(
        "0 recorded Google accounts are authorized through this rule", exact=False
    ).count()
    page.goto(component_origin + "/portal-users-refused")
    assert (
        page.get_by_role("alert")
        .get_by_text("consumer email domain", exact=False)
        .count()
    )
    assert page.get_by_role("link", name="Return to Sign-in rules").count() == 1

    page.goto(component_origin + "/portal-users-confirmed")
    chair = page.get_by_role("row", name="chair@example.org", exact=False)
    assert (
        chair.get_by_text("The Ministry leader role is suspended", exact=False).count()
        == 0
    )
    page.goto(component_origin + "/portal-users-minimal")
    visible(page.get_by_text("No hosted-domain rules.", exact=True))
    assert (
        page.get_by_role("region", name="Ministry assignments", exact=False).count()
        == 0
    )


def _review_request(page, button):
    """Click ``button`` (with the rules route answered) and return its POST."""
    with page.expect_request(lambda request: request.method == "POST") as sent:
        button.click()
    # Let the routed answer finish loading before the next navigation,
    # which it would otherwise interrupt (#623).
    visible(page.get_by_text("Reviewed", exact=True))
    assert "?" not in sent.value.url and sent.value.url.endswith(
        reverse("admin:user_rules")
    )
    return sent.value.post_data


def test_rule_review_forms_post_to_the_rules_route(page, component_origin):
    """Each row's review buttons and the add forms post their identifying
    values in the body to the rules route, never a URL.

    "Review removal" always posts natively. "Review role change" does too
    when users-v1.js exits early in an older browser (no
    crypto.randomUUID); otherwise row ticks autosave
    (test_rule_autosave.py).
    """
    page.route(
        "**" + reverse("admin:user_rules"), lambda route: route.fulfill(body="Reviewed")
    )
    page.goto(component_origin + "/portal-users")
    leader = page.get_by_role("row", name="leader@workspace.example", exact=False)
    body = _review_request(page, leader.get_by_role("button", name="Review removal"))
    assert "kind=address" in body and "identity=leader%40workspace.example" in body
    assert "operation=remove" in body and "roles=" in body
    assert "action=preview" in body and "base_digest=" in body

    page.add_init_script("delete Crypto.prototype.randomUUID")
    page.goto(component_origin + "/portal-users")
    leader = page.get_by_role("row", name="leader@workspace.example", exact=False)
    leader.get_by_label("Ministry leader").uncheck()
    body = _review_request(
        page, leader.get_by_role("button", name="Review role change")
    )
    assert "kind=address" in body and "identity=leader%40workspace.example" in body
    assert "operation=set" in body and "roles=staff" in body
    assert "roles=ministry_leader" not in body and "action=preview" in body
    assert "base_digest=" in body

    page.goto(component_origin + "/portal-users")
    page.get_by_label("Hosted domain").fill("Parish.Example")
    page.get_by_role("group", name="Add a hosted-domain rule").get_by_label(
        "Staff"
    ).check()
    body = _review_request(
        page, page.get_by_role("button", name="Review new domain rule")
    )
    assert "kind=domain" in body and "identity=Parish.Example" in body
    assert "roles=staff" in body and "roles=administrator" not in body
