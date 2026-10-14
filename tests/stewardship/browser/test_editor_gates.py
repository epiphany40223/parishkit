"""Settings editors wait for a change and a complete entry (#1330, #563).

In real browsers: integration Save waits for a change (a pasted key counts),
Off-site backups' Test access never does, Slack's Save is unavailable while
its key can't be stored, ParishSoft's Save waits beside its schedule gate,
the Pages and emails editor's Preview changes waits for a change (the
visual editor's toolbar counts), New user's Review waits for an Email and a
Name and for the Email check, the automation approval's user code and the
Parish website are checked as typed, and Campaign settings' leader roles
need one ticked. Pages are the real templates (the components modules and
conftest); the server's own checks are covered beside the views.
"""

import pytest

from .automation_components import APPROVAL
from .campaign_settings_components import DRAFT
from .user_components import NEW, NEW_TAKEN
from .waits import has_attribute, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"


def watch(page):
    """Fail on any script error."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    return failures


def test_integration_save_waits_for_a_change(page, component_origin):
    """Set-up Slack's Save waits, with the shared hint, until a setting or
    a pasted key differs from what is saved; undoing the edit holds it
    again, also when the page is shown again from history."""
    from playwright.sync_api import expect

    failures = watch(page)
    page.goto(component_origin + "/slack-set-up")
    save = page.get_by_role("button", name="Save", exact=True)
    hint = page.locator("[data-unchanged-hint]")
    expect(save).to_be_disabled()
    expect(hint).to_have_css("visibility", "visible")
    channel = page.get_by_label("Channel ID")
    channel.fill("C0000000001")
    expect(save).to_be_enabled()
    expect(hint).to_have_css("visibility", "hidden")
    channel.fill("C0123456789")
    expect(save).to_be_disabled()
    page.locator("input[name=candidate]").fill("xoxb-new-token")
    expect(save).to_be_enabled()
    page.evaluate(RESTORE)
    expect(save).to_be_enabled()
    assert not failures


def test_test_access_never_waits_for_a_change(page, component_origin):
    """Off-site backups' Test access tests the saved folder, so only Save
    waits for a change; a link that isn't on Drive holds both, with the
    field's hint, and is marked at the field when left."""
    from playwright.sync_api import expect

    failures = watch(page)
    page.goto(component_origin + "/backup-set-up")
    save = page.get_by_role("button", name="Save", exact=True)
    test = page.get_by_role("button", name="Test access")
    expect(save).to_be_disabled()
    expect(test).to_be_enabled()
    link = page.get_by_label("Google Drive folder link")
    link.fill("https://example.org/folder")
    link.press("Tab")
    expect(save).to_be_disabled()
    expect(test).to_be_disabled()
    has_attribute(link, "aria-invalid", "true")
    hint = page.locator("#integration-complete-hint")
    visible(hint)
    assert hint.inner_text() == "Paste the Google Drive folder link."
    link.fill("https://drive.google.com/drive/folders/" + "b" * 20)
    expect(save).to_be_enabled()
    expect(test).to_be_enabled()
    assert not failures


def test_slack_save_is_unavailable_while_its_key_cannot_be_stored(
    page, component_origin
):
    """Slack not set up needs its key, which can't be stored now: Save is
    drawn unavailable, named by the notice that says why, and no entry
    brings it back."""
    from playwright.sync_api import expect

    failures = watch(page)
    page.goto(component_origin + "/slack-key-unavailable")
    save = page.get_by_role("button", name="Save", exact=True)
    expect(save).to_be_disabled()
    notice = page.locator("#integration-key-unavailable")
    visible(notice)
    assert save.get_attribute("aria-describedby") == "integration-key-unavailable"
    page.get_by_label("Channel ID").fill("C0000000001")
    expect(save).to_be_disabled()
    assert not failures


def test_parishsoft_save_waits_for_a_change_beside_its_schedule(page, component_origin):
    """ParishSoft's Save waits for a change too; a pasted key is one, and
    the schedule editor's own gate never releases what the change gate
    holds."""
    from playwright.sync_api import expect

    failures = watch(page)
    page.goto(component_origin + "/refresh-schedule-gated")
    save = page.locator(".schedule-save button[type=submit]")
    expect(save).to_be_disabled()
    key = page.locator("input[name=candidate]")
    key.fill("new-parishsoft-key")
    expect(save).to_be_enabled()
    key.fill("")
    expect(save).to_be_disabled()
    # The schedule gate re-checks on pageshow; the change gate still holds.
    page.evaluate(RESTORE)
    expect(save).to_be_disabled()
    assert not failures


def test_preview_changes_waits_for_a_change(page, component_origin):
    """Preview changes waits until the content differs from what is saved;
    a toolbar edit in the visual editor, which fires no input event, counts.
    Generated plain text is no change."""
    from playwright.sync_api import expect

    failures = watch(page)
    page.goto(component_origin + "/content-settings-gated")
    preview = page.get_by_role("button", name="Preview changes")
    hint = page.locator("[data-unchanged-hint]")
    expect(preview).to_be_disabled()
    expect(hint).to_have_text("Change the content to preview it.")
    subject = page.get_by_label("Subject")
    subject.fill("Thank you")
    expect(preview).to_be_enabled()
    subject.fill("Received")
    expect(preview).to_be_disabled()
    editor = page.locator("[data-content-editor]")
    editor.click()
    page.keyboard.press("ControlOrMeta+a")
    page.get_by_role("button", name="Bold").click()
    expect(preview).to_be_enabled()
    assert not failures


def test_new_user_review_waits_for_an_email_and_a_name(page, component_origin):
    """New user's Review waits for an Email and a Name, saying which, in a
    line reserved under the buttons."""
    from playwright.sync_api import expect

    failures = watch(page)
    page.goto(component_origin + NEW)
    review = page.get_by_role("button", name="Add the user")
    hint = page.locator("#user-complete-hint")
    expect(review).to_be_disabled()
    assert hint.inner_text() == "Enter the email address this person signs in with."
    email = page.get_by_label("Email", exact=True)
    email.fill("not an address")
    expect(hint).to_have_text("Enter one email address, such as name@example.org.")
    email.fill("new@example.org")
    page.get_by_label("Name", exact=True).fill("")
    expect(hint).to_have_text("Enter a name.")
    page.get_by_label("Name", exact=True).fill("New, Nora")
    expect(review).to_be_enabled()
    assert hint.inner_text() == ""
    assert not failures


def test_an_address_already_a_user_holds_review(page, component_origin):
    """The Email check's refusal at the field holds Review, with its words,
    until the address is edited."""
    from playwright.sync_api import expect

    failures = watch(page)
    page.goto(component_origin + NEW_TAKEN)
    review = page.get_by_role("button", name="Add the user")
    page.get_by_label("Name", exact=True).fill("Staff, Sam")
    email = page.get_by_label("Email", exact=True)
    email.fill("staff@example.org")
    email.press("Tab")
    has_attribute(page.locator("[data-user-email]"), "aria-invalid", "true")
    expect(review).to_be_disabled()
    assert "already a user" in page.locator("#user-complete-hint").inner_text()
    email.fill("other@example.org")
    expect(review).to_be_enabled()
    assert not failures


def test_the_user_code_is_checked_as_it_is_typed(page, component_origin):
    """Continue waits for a code the server could read: eight of its
    letters and digits, in either case, with any hyphen or spaces."""
    from playwright.sync_api import expect

    failures = watch(page)
    page.goto(component_origin + APPROVAL)
    proceed = page.get_by_role("button", name="Continue")
    code = page.get_by_role("textbox", name="User code")
    expect(proceed).to_be_disabled()
    code.fill("abcd-efg")
    expect(proceed).to_be_disabled()
    expect(page.locator("#automation-code-hint")).to_have_text(
        "Enter the user code the command line showed, such as ABCD-EFGH."
    )
    code.fill("abcd efgh")
    expect(proceed).to_be_enabled()
    code.fill("ABCD-EFGO")
    expect(proceed).to_be_disabled()
    assert not failures


def test_the_parish_website_is_checked_in_the_browser(page, component_origin):
    """A website with a query, a fragment or a user name is marked at the
    field when left; a plain address is not."""
    failures = watch(page)
    page.goto(component_origin + "/parish-settings")
    website = page.get_by_label("Parish website")
    for value in ("https://example.org/?a=1", "https://u@example.org/"):
        website.fill(value)
        website.press("Tab")
        has_attribute(website, "aria-invalid", "true")
    website.fill("https://example.org/parish")
    website.press("Tab")
    has_attribute(website, "aria-invalid", "false")
    assert not failures


def test_leader_roles_need_one_ticked(page, component_origin):
    """While Ministry stewardship is on, Review waits for a leader role;
    turning Ministry stewardship off asks for none."""
    from playwright.sync_api import expect

    failures = watch(page)
    page.goto(component_origin + DRAFT)
    review = page.get_by_role("button", name="Save", exact=True)
    ministry = page.get_by_label("Ministry stewardship")
    if not ministry.is_checked():
        ministry.check()
    roles = page.get_by_role("group", name="Ministry leader roles")
    for box in roles.get_by_role("checkbox").all():
        box.uncheck()
    expect(review).to_be_disabled()
    expect(page.locator("#settings-complete-hint")).to_have_text(
        "Choose at least one role."
    )
    page.get_by_label("Census").check()
    ministry.uncheck()
    # Only the change gate may still hold Review: the roles ask for nothing.
    expect(
        page.locator("#settings-form button[type=submit][data-complete-gated]")
    ).to_have_count(0)
    assert not failures
