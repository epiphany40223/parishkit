"""Administrator review and reviewed edits of login rules."""

import pytest

from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

PAGES = (
    "/portal-users",
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
    # (Not exact: the cell also holds the Sign-in activity button, #953.)
    assert blocked.get_by_text("None on record", exact=False).count() == 1
    assert blocked.locator("time").count() == 0
    # Its sign-in activity, refusals included (#953), opens System logs by a
    # POST naming the identity in the body, never in an address.
    history = blocked.get_by_role(
        "button", name="Sign-in activity for blocked@example.org", exact=True
    )
    visible(history)
    form = history.locator("xpath=ancestor::form")
    assert form.get_attribute("action") == "/admin/system/logs/"
    assert form.get_attribute("method") == "post"
    assert form.locator("input[name=activity]").get_attribute("value") == "sign_in"
    # A rule whose address no Google identity has used has no such link.
    unseen = page.get_by_role("row", name="chair@example.org", exact=False)
    assert (
        unseen.get_by_role("button", name="Sign-in activity", exact=False).count() == 0
    )
    # A rule's Ministry leader role is held, whatever its origin (2026-10-10).
    chair = page.get_by_role("row", name="chair@example.org", exact=False)
    assert chair.get_by_role("cell", name="Ministry leader", exact=True).count()
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
    # Ministry assignment records from before #922 are not shown at all.
    for address in ("stray@elsewhere.example", "consumer@workspace.example"):
        assert page.get_by_role("row", name=address, exact=False).count() == 0
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
    assert page.get_by_role("link", name="Return to Portal users").count() == 1

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
    assert "?" not in sent.value.url and sent.value.url.endswith("/users/rules")
    return sent.value.post_data


def test_rule_review_forms_post_to_the_rules_route(page, component_origin):
    """Each row's review buttons and the add forms post their identifying
    values in the body to the rules route, never a URL.

    "Review removal" always posts natively. "Review role change" does too
    when users-v1.js exits early in an older browser (no
    crypto.randomUUID); otherwise row ticks autosave
    (test_rule_autosave.py).
    """
    page.route("**/users/rules", lambda route: route.fulfill(body="Reviewed"))
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


def test_upgrade_preview_panel_lists_the_kept_administrators(page, component_origin):
    """The read-only users upgrade preview (#952 slice 2) on Portal users.

    It lists the exact Administrators the upgrade keeps first, then the users
    it writes with where each comes from, and what it drops or leaves behind.
    It offers no control that changes anything, and its table's headings
    sort it on the server, keeping the other tables' places.
    """
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(component_origin + "/portal-users")
    panel = page.get_by_role("region", name="Upgrade preview", exact=True)
    # A link near the heading reaches the panel at the bottom of the page.
    page.get_by_role("link", name="See the users upgrade preview").click()
    visible(panel)
    assert page.evaluate("location.hash") == "#upgrade-preview"
    kept = panel.get_by_role("list", name="Administrators kept")
    assert kept.get_by_role("listitem").all_inner_texts() == ["admin@example.org"]
    visible(panel.get_by_text("This address keeps Administrator.", exact=False))
    # The users table: who the upgrade writes, and from which rule.
    users = panel.get_by_role("region", name="Users after the upgrade")
    rows = users.get_by_role("row")
    # The heading row, then admin, chair, helper and leader by address.
    assert rows.count() == 5
    helper = users.get_by_role("row", name="helper@workspace.example", exact=False)
    assert helper.get_by_role("cell", name="Helper, Hal", exact=True).count() == 1
    assert helper.get_by_role("cell", name="Staff", exact=True).count() == 1
    assert (
        helper.get_by_role(
            "cell", name="Hosted-domain rule workspace.example", exact=True
        ).count()
        == 1
    )
    leader = users.get_by_role("row", name="leader@workspace.example", exact=False)
    assert (
        leader.get_by_role(
            "cell", name="Exact-address rule (Staff, Ministry leader)", exact=True
        ).count()
        == 1
    )
    # The domain rules, the dropped deny rule and what is not carried over.
    domains = panel.get_by_role("list", name="Hosted-domain rules")
    assert domains.get_by_role("listitem").all_inner_texts() == [
        "unused.example (Staff): 0 people become Staff users.",
        "workspace.example (Staff, Ministry leader): 1 person becomes a Staff user.",
    ]
    denied = panel.get_by_role("list", name="Deny rules dropped")
    assert denied.get_by_role("listitem").all_inner_texts() == [
        "blocked@example.org (blocked): no recorded Google account uses this address."
    ]
    assert panel.get_by_role(
        "list", name="Disabled Google accounts", exact=False
    ).get_by_role("listitem").all_inner_texts() == ["None."]
    # Read-only: beyond its table's page navigator (GET forms), nothing in
    # the panel posts or takes a choice.
    assert panel.locator("form[method=post], form[method=POST]").count() == 0
    assert panel.locator("form:not([method=get])").count() == 0
    assert panel.locator("input[type=checkbox], input[type=radio]").count() == 0
    # Its headings sort on the server under their own prefix (#478, a full
    # load here), carrying the other tables' places.
    role = users.get_by_role("link", name="Role", exact=False)
    href = role.get_attribute("href")
    assert "upgrade_sort=-role" in href and "domains_page=1" in href
    assert href.endswith("#upgrade-table")


def test_minimal_upgrade_preview_keeps_the_one_administrator(page, component_origin):
    """With only the mandatory Administrator, the panel still names them."""
    page.goto(component_origin + "/portal-users-minimal")
    panel = page.get_by_role("region", name="Upgrade preview", exact=True)
    kept = panel.get_by_role("list", name="Administrators kept")
    assert kept.get_by_role("listitem").all_inner_texts() == ["admin@example.org"]
    for name in ("Hosted-domain rules", "Deny rules dropped"):
        assert panel.get_by_role("list", name=name).get_by_role(
            "listitem"
        ).all_inner_texts() == ["None."]
