"""Actions the server would refuse wait, and say why (#563, slice 3).

A setup credential step keeps Save and continue unavailable while an earlier
step is not done, named by the reason; otherwise it waits for a key the
server needs. ParishSoft connection's organization choice waits for a
selectable organization (#1554). Send to chosen Families waits for a DUID
before Check, and shows Send unavailable with its reason. Each hint sits
after its button, so it never moves the control just used, and every state
is worked out again on pageshow, when a page restored from history gets
back its values without events.
"""

import pytest

from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"


def spot(locator):
    """The element's page position in whole pixels, to show it did not move."""
    return tuple(
        round(value)
        for value in locator.evaluate(
            "e => { const box = e.getBoundingClientRect();"
            " return [box.left + window.scrollX, box.top + window.scrollY]; }"
        )
    )


def waits_with(button, hint, text):
    """Assert the button is unavailable and its linked hint says ``text``."""
    assert button.is_disabled()
    visible(hint)
    assert hint.text_content() == text
    assert hint.get_attribute("id") in button.get_attribute("aria-describedby").split()


def test_a_kept_parishsoft_key_saves_without_a_new_key(page, component_origin):
    """Save stays available to keep the key; the page names its organization
    and has no organization field (#1554)."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/setup-credential-kept")
    save = page.get_by_role("button", name="Save and continue")
    hint = page.locator("#setup-complete-hint")
    assert save.is_enabled()
    hidden(hint)
    assert page.locator("#id_organization_id").count() == 0
    assert "A ParishSoft API key is saved for St. Example Parish (4321)." in (
        page.locator(".setup-saved").text_content()
    )
    page.get_by_label("ParishSoft API key").fill("synthetic-key")
    assert save.is_enabled()
    page.evaluate(RESTORE)
    assert save.is_enabled()
    assert failures == []


def test_use_this_organization_waits_for_a_selectable_choice(page, component_origin):
    """The greyed organization cannot be chosen; Use waits for a choice,
    while Cancel and Check again need none (#1554)."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/setup-organization-choice")
    use = page.get_by_role("button", name="Use this organization")
    hint = page.locator("#setup-complete-hint")
    visible(hint)
    assert use.is_disabled()
    assert page.get_by_role("button", name="Cancel").is_enabled()
    assert page.get_by_role("button", name="Check again").is_enabled()
    assert page.get_by_label("Diocesan School (8)").is_disabled()
    where = spot(use)
    page.get_by_label("Mission Chapel (9)").check()
    assert use.is_enabled()
    hidden(hint)
    assert spot(use) == where
    assert page.locator("#organization-filter").count() == 0
    assert failures == []


def test_a_long_list_filters_by_name_or_id_and_keeps_the_choice(page, component_origin):
    """Above 20 organizations a filter box narrows the list (#1554)."""
    page.goto(component_origin + "/setup-organization-filter")
    choices = page.locator("#organization-choices li")
    page.get_by_label("Parish 05 (5)").check()
    page.get_by_label("Find an organization by name or ID").fill("parish 1")
    shown = [item.text_content().strip() for item in choices.all() if item.is_visible()]
    assert shown == [f"Parish {n} ({n})" for n in range(10, 20)]
    page.get_by_label("Find an organization by name or ID").fill("(22)")
    assert [item.is_visible() for item in choices.all()].count(True) == 1
    page.get_by_label("Find an organization by name or ID").fill("")
    assert all(item.is_visible() for item in choices.all())
    assert page.get_by_label("Parish 05 (5)").is_checked()
    assert page.get_by_role("button", name="Use this organization").is_enabled()


def test_a_blocked_credential_step_shows_save_unavailable_with_its_reason(
    page, component_origin
):
    """Save is disabled from the start, described by the visible prerequisite,
    and pasting a key does not make it available."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/setup-credential-blocked")
    save = page.get_by_role("button", name="Save and continue")
    reason = page.locator("#credential-prerequisite")
    visible(reason)
    assert "Save the outgoing email settings first." in reason.text_content()
    assert save.is_disabled()
    assert save.get_attribute("aria-describedby") == "credential-prerequisite"
    page.get_by_label("Service-account JSON key file contents").fill("{}")
    page.evaluate(RESTORE)
    assert save.is_disabled()
    assert page.locator("[data-complete-hint]").count() == 0
    assert failures == []


def test_family_tests_wait_for_a_duid_and_show_send_unavailable_with_why(
    page, component_origin
):
    """Check waits while the DUID box is empty or only spaces; Send is shown
    unavailable, named by the reason it cannot be sent."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/family-tests-check")
    check = page.get_by_role("button", name="Check these Families")
    hint = page.locator("#family-test-check-hint")
    box = page.get_by_label("Family DUIDs, one per line (at most ten)")
    waits_with(check, hint, "Enter at least one Family DUID to check.")
    where = (spot(box), spot(check))
    box.fill("   ")
    waits_with(check, hint, "Enter at least one Family DUID to check.")
    box.fill("1234")
    assert check.is_enabled()
    hidden(hint)
    assert (spot(box), spot(check)) == where
    box.evaluate("node => { node.value = ''; }")
    page.evaluate(RESTORE)
    waits_with(check, hint, "Enter at least one Family DUID to check.")
    send = page.get_by_role("button", name="Send these Family tests")
    assert send.is_disabled()
    reason = page.locator("#family-test-unavailable")
    visible(reason)
    assert reason.text_content() == "Only 1 more Family test may be requested now."
    assert send.get_attribute("aria-describedby") == "family-test-unavailable"
    assert failures == []
