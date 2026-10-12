"""Integration, backup key and Share options forms in the browser (#563).

Slack not set up waits for its channel and token; off-site backups shows
Test access unavailable with why until Google Workspace mail is set up; both
steps of a backup key change wait for a well-formed entry; and a Share
options row ticked Delete greys its fields in place, so nothing below moves.
Each hint sits after its button, and every state is worked out again on
pageshow, when a page restored from history gets its values back without
change events. The rendered markup is pinned in
test_conditional_integrations.py.
"""

import pytest

from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
CHANNEL_HINT = "Enter a Slack channel ID, such as C0123456789."
TOKEN_HINT = "Paste the Slack bot token to set up Slack."
KEY_HINT = "Paste the public_key value: one line of 44 characters ending in =."
CODE_HINT = "Type the 12-character code backup-prove printed."
# A canonical X25519 public key's shape: 43 base64 characters and "=".
PUBLIC_KEY = "q83vEjRWeJCrze8SNFZ4kKvN7xI0VniQq83vEjRWeAA="


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


def ready(button, hint):
    """Assert the button is available and its hint is gone."""
    assert button.is_enabled()
    hidden(hint)


def test_slack_not_set_up_waits_for_its_channel_and_token(page, component_origin):
    """Save waits for a well-formed channel ID, then for the token; a page
    restored from history with both filled is ready again."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/slack-not-set-up")
    save = page.get_by_role("button", name="Save", exact=True)
    hint = page.locator("#integration-complete-hint")
    channel = page.get_by_label("Slack channel ID")
    token = page.get_by_label("Slack bot token")
    waits_with(save, hint, CHANNEL_HINT)
    where = spot(save)
    channel.fill("general")
    waits_with(save, hint, CHANNEL_HINT)
    channel.fill("C0123ABC")
    waits_with(save, hint, TOKEN_HINT)
    token.fill("   ")
    waits_with(save, hint, TOKEN_HINT)
    token.fill("xoxb-synthetic")
    ready(save, hint)
    assert spot(save) == where
    # Back to the page with the token cleared, without events, then restored.
    token.evaluate("e => { e.value = ''; }")
    page.evaluate(RESTORE)
    waits_with(save, hint, TOKEN_HINT)
    token.evaluate("e => { e.value = 'xoxb-synthetic'; }")
    page.evaluate(RESTORE)
    ready(save, hint)
    assert not failures


def test_test_access_is_unavailable_with_why_before_workspace_mail(
    page, component_origin
):
    """Test access is disabled and names its reason; Save waits for the link."""
    page.goto(component_origin + "/backup-no-workspace")
    test = page.get_by_role("button", name="Test access")
    assert test.is_disabled()
    reason = page.locator("#backup-test-reason")
    visible(reason)
    assert "Google Workspace mail to be set up first" in reason.text_content()
    assert test.get_attribute("aria-describedby") == "backup-test-reason"
    assert reason.get_by_role("link", name="Set up Google Workspace mail").is_visible()
    save = page.get_by_role("button", name="Save", exact=True)
    hint = page.locator("#integration-complete-hint")
    waits_with(save, hint, "Paste the Google Drive folder link.")
    page.get_by_label("Google Drive folder link").fill(
        "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUv"
    )
    ready(save, hint)
    # The gate never takes over the server's unavailable Test access.
    assert test.is_disabled()


def test_a_new_backup_key_waits_for_a_well_formed_public_key(page, component_origin):
    """Continue waits for one 44-character line ending in =."""
    page.goto(component_origin + "/backup-key-new")
    proceed = page.get_by_role("button", name="Continue")
    hint = page.locator("#backup-key-public-hint")
    key = page.get_by_label("New public key")
    waits_with(proceed, hint, KEY_HINT)
    where = spot(proceed)
    key.fill('{"public_key": "' + PUBLIC_KEY + '"}')
    waits_with(proceed, hint, KEY_HINT)
    key.fill(PUBLIC_KEY[:-1])
    waits_with(proceed, hint, KEY_HINT)
    key.fill(PUBLIC_KEY + " ")
    ready(proceed, hint)
    assert spot(proceed) == where


def test_the_backup_prove_code_waits_for_all_twelve_characters(page, component_origin):
    """Check code waits for twelve characters, typed any way the server reads."""
    page.goto(component_origin + "/backup-key-code")
    check = page.get_by_role("button", name="Check code and review")
    hint = page.locator("#backup-key-code-hint")
    code = page.get_by_label("Code from backup-prove")
    waits_with(check, hint, CODE_HINT)
    code.fill("abcd-2345")
    waits_with(check, hint, CODE_HINT)
    code.fill("abcd-2345-efgh")
    ready(check, hint)
    code.fill("ABCD 2345 EFGH")
    ready(check, hint)


def test_a_share_row_ticked_delete_greys_its_fields_in_place(page, component_origin):
    """Delete greys the row's label, free text and order without moving
    anything; unticking gives them back; a restored tick greys them again."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/share-settings")
    label = page.locator("#id_options-1-label")
    free = page.locator("#id_options-1-free_text")
    order = page.locator("#id_options-1-ORDER")
    delete = page.locator("#id_options-1-DELETE")
    after = page.locator("#id_options-2-label")
    where = (spot(label), spot(after), spot(delete))
    delete.check()
    for control in (label, free, order):
        assert control.is_disabled()
    assert page.locator("#id_options-1-id").is_enabled()
    assert (spot(label), spot(after), spot(delete)) == where
    # Other rows are untouched.
    assert after.is_enabled()
    delete.uncheck()
    for control in (label, free, order):
        assert control.is_enabled()
    assert label.input_value()
    delete.evaluate("e => { e.checked = true; }")
    page.evaluate(RESTORE)
    assert label.is_disabled()
    assert not failures
