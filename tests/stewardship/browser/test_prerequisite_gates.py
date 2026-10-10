"""Actions the server would refuse wait, and say why (#563, slice 3).

A setup credential step keeps Save and continue unavailable while an earlier
step is not done, named by the reason; otherwise it waits for a key the
server needs, including a kept ParishSoft key once the organization ID
changes. Send to chosen Families waits for a DUID before Check, and shows
Send unavailable with its reason. Each hint sits after its button, so it
never moves the control just used, and every state is worked out again on
pageshow, when a page restored from history gets back its values without
events.
"""

import pytest

from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
ORGANIZATION_HINT = (
    "To change the organization ID, enter the ParishSoft API key again as well."
)


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


def test_a_kept_parishsoft_key_is_needed_again_once_the_organization_changes(
    page, component_origin
):
    """Save stays available to keep the key, waits once the organization
    differs, and is available again with a new key or the old organization."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/setup-credential-kept")
    save = page.get_by_role("button", name="Save and continue")
    hint = page.locator("#setup-complete-hint")
    organization = page.get_by_label("Expected ParishSoft organization ID")
    key = page.get_by_label("ParishSoft API key")
    assert save.is_enabled()
    hidden(hint)
    where = (spot(organization), spot(save))
    organization.fill("98765")
    waits_with(save, hint, ORGANIZATION_HINT)
    assert (spot(organization), spot(save)) == where
    key.fill("synthetic-key")
    assert save.is_enabled()
    hidden(hint)
    key.fill("")
    organization.fill("4321")
    assert save.is_enabled()
    # A restored organization, with no events, is checked again on pageshow.
    organization.evaluate("node => { node.value = '555'; }")
    page.evaluate(RESTORE)
    waits_with(save, hint, ORGANIZATION_HINT)
    assert failures == []


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


def test_prepare_family_links_is_shown_unavailable_with_its_reason(
    page, component_origin
):
    """With a preparation listed, Prepare is greyed and says why."""
    page.goto(component_origin + "/go-live-links")
    prepare = page.get_by_role("button", name="Prepare inactive Family links")
    assert prepare.is_disabled()
    reason = page.locator("#prepare-unavailable")
    visible(reason)
    assert "cancel and discard those first" in reason.text_content()
    assert prepare.get_attribute("aria-describedby") == "prepare-unavailable"
