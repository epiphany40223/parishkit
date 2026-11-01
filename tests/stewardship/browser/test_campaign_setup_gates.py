"""Campaign and Slack setup forms wait for what the server needs (#563).

Campaign settings and Setup › First campaign keep their action unavailable
until a campaign module is ticked and, with Financial stewardship ticked,
both financial periods and their funds are filled in, with the commitment
period starting after the campaign. Campaign settings' Review also waits
for a change from the saved settings (#921), so its tests make a real edit
before the complete gate is the one deciding, and one test checks the two
gates together. Setup › Slack shows the channel ID only while Slack is
enabled, and then waits for it. Each hint sits after its button, so a line
coming or going never moves the control just clicked, or the button. Every
state is worked out again on pageshow, when a page restored from history gets back its
values without change events.
"""

import re

import pytest

from .waits import available, has_attribute, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
MODULE_HINT = (
    "Choose at least one: Census, Ministry stewardship or Financial stewardship."
)
FINANCIAL_HINT = "Fill in the commitment and comparison periods and their funds."
COMMITMENT_HINT = "Choose a commitment period start after the campaign end date."
SLACK_HINT = "Enter the Slack channel ID, or untick Enable Slack notifications."
UNCHANGED_HINT = "Change a setting to save it."
END_HINT = "Choose a campaign end date after its start date."


def restore(locator, script):
    """Change a control without events, as a restore does, then show the page."""
    locator.evaluate(script)
    locator.page.evaluate(RESTORE)


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
    """Assert the button is unavailable and its linked hint says ``text``.

    Every read waits (auto-waiting assertions): the page's change handlers run
    a tick after an input event, so an immediate read can see the old state
    (#2289).
    """
    from playwright.sync_api import expect

    expect(button).to_be_disabled()
    visible(hint)
    expect(hint).to_have_text(text)
    expect(button).to_have_attribute(
        "aria-describedby", re.compile(rf"(^|\s){hint.get_attribute('id')}(\s|$)")
    )


def ready(button, hint):
    """Assert the button is available and its hint is gone, waiting."""
    available(button)
    hidden(hint)


def unchanged(page, button):
    """Assert the change gate holds Review: unavailable, its hint shown and
    describing the button (#921)."""
    line = page.locator("#settings-form-unchanged-hint")
    assert button.is_disabled()
    visible(line)
    assert line.text_content() == UNCHANGED_HINT
    assert line.get_attribute("id") in button.get_attribute("aria-describedby").split()


def changed(page):
    """Assert the change gate no longer holds Review: its hint is idle."""
    hidden(page.locator("#settings-form-unchanged-hint"))


def test_campaign_settings_waits_for_a_module_and_the_financial_fields(
    page, component_origin
):
    """Review needs a module and, for Financial stewardship, every period
    field and fund, with the commitment period starting after the campaign;
    nothing clicked moves."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/campaign-settings")
    review = page.get_by_role("button", name="Save", exact=True)
    hint = page.locator("#settings-complete-hint")
    census = page.locator("#id_census")
    financial = page.locator("#id_financial_enabled")
    # Complete but unchanged: only the change gate holds Review (#921), so a
    # real edit comes first and the complete gate decides from here on.
    unchanged(page, review)
    hidden(hint)
    page.get_by_label("Campaign name").fill("Renamed campaign")
    ready(review, hint)
    where = (spot(census), spot(review))
    # No module: Review waits, and neither the box nor the button moves.
    census.uncheck()
    waits_with(review, hint, MODULE_HINT)
    assert (spot(census), spot(review)) == where
    census.check()
    ready(review, hint)
    assert (spot(census), spot(review)) == where
    # Financial stewardship shows its fields below the box, which stays put.
    box = spot(financial)
    financial.check()
    assert spot(financial) == box
    visible(page.get_by_role("group", name="Commitment and comparison periods"))
    waits_with(review, hint, FINANCIAL_HINT)
    page.get_by_label("Commitment period start").fill("2027-01-01")
    page.get_by_label("Comparison financial period start").fill("2025-01-01")
    page.get_by_label("Pledge funds").select_option("9")
    assert review.is_disabled()
    page.get_by_label("Comparison financial period funds").select_option("9")
    ready(review, hint)
    # A commitment period starting before the campaign (October 2026)
    # ends waits with its own hint (#2293); none asks for a confirmation.
    page.get_by_label("Commitment period start").fill("2026-01-01")
    waits_with(review, hint, COMMITMENT_HINT)
    assert page.locator("[data-overlap-confirmation]").count() == 0
    page.get_by_label("Commitment period start").fill("2027-01-01")
    ready(review, hint)
    # Unticking Financial stewardship hides and drops every financial field.
    page.get_by_label("Commitment period start").fill("")
    assert review.is_disabled()
    financial.uncheck()
    # Its disabled fields no longer count, and the renamed campaign is still
    # a change, so Review is available again.
    ready(review, hint)
    assert not failures


def test_campaign_settings_rechecks_restored_modules(page, component_origin):
    """A module tick restored from history, without an event, is counted."""
    page.goto(component_origin + "/campaign-settings")
    review = page.get_by_role("button", name="Save", exact=True)
    hint = page.locator("#settings-complete-hint")
    # A real edit first, so restoring Census below makes Review available
    # again rather than leaving the saved settings unchanged (#921).
    page.get_by_label("Campaign name").fill("Renamed campaign")
    ready(review, hint)
    restore(page.locator("#id_census"), "box => { box.checked = false; }")
    waits_with(review, hint, MODULE_HINT)
    # Financial stewardship restored with its fields still empty.
    restore(page.locator("#id_financial_enabled"), "box => { box.checked = true; }")
    waits_with(review, hint, FINANCIAL_HINT)
    restore(page.locator("#id_financial_enabled"), "box => { box.checked = false; }")
    waits_with(review, hint, MODULE_HINT)
    restore(page.locator("#id_census"), "box => { box.checked = true; }")
    ready(review, hint)
    # The saved name restored too: complete again, but nothing changed.
    restore(
        page.get_by_label("Campaign name"),
        "field => { field.value = 'Sample campaign'; }",
    )
    unchanged(page, review)
    hidden(hint)


def test_campaign_settings_review_waits_for_a_change_and_completeness(
    page, component_origin
):
    """Review is available only when the form both differs from the saved
    settings and is complete; releasing either gate alone leaves it held."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    # Saved with no module: unchanged and incomplete, so both gates hold.
    page.goto(component_origin + "/campaign-settings-no-module")
    review = page.get_by_role("button", name="Save", exact=True)
    hint = page.locator("#settings-complete-hint")
    name = page.get_by_label("Campaign name")
    census = page.locator("#id_census")
    waits_with(review, hint, MODULE_HINT)
    unchanged(page, review)
    # An edit releases only the change gate: still incomplete, still held.
    name.fill("Renamed campaign")
    changed(page)
    waits_with(review, hint, MODULE_HINT)
    # A module ticked as well releases both.
    census.check()
    ready(review, hint)
    changed(page)
    # Saved complete: unchanged on load, so completeness alone holds it.
    page.goto(component_origin + "/campaign-settings")
    hidden(hint)
    unchanged(page, review)
    # Unticking and reticking Census returns to the saved settings: held.
    census.uncheck()
    waits_with(review, hint, MODULE_HINT)
    census.check()
    hidden(hint)
    unchanged(page, review)
    # A real edit on the complete form releases both.
    name.fill("Renamed campaign")
    ready(review, hint)
    changed(page)
    assert not failures


def test_campaign_settings_waits_for_an_end_date_after_its_start(
    page, component_origin
):
    """An end date before the start holds Review with the end date's hint,
    as soon as it is entered and again on pageshow; the start sets the end's
    min (data-not-before), so the date picker offers no earlier day (#563)."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/campaign-settings")
    review = page.get_by_role("button", name="Save", exact=True)
    hint = page.locator("#settings-complete-hint")
    start = page.get_by_label("Campaign start date")
    end = page.get_by_label("Campaign end date")
    assert end.get_attribute("min") == "2026-10-01"
    where = (spot(end), spot(review))
    end.fill("2026-09-15")
    waits_with(review, hint, END_HINT)
    assert (spot(end), spot(review)) == where
    end.fill("2026-11-15")
    ready(review, hint)
    # A later start moves the end's min with it.
    start.fill("2026-11-20")
    assert end.get_attribute("min") == "2026-11-20"
    waits_with(review, hint, END_HINT)
    start.fill("2026-10-01")
    ready(review, hint)
    # A start restored from history, without an event, is checked again.
    restore(start, "field => { field.value = '2026-12-01'; }")
    waits_with(review, hint, END_HINT)
    assert not failures


def test_slack_channel_shows_and_is_required_only_while_enabled(page, component_origin):
    """The channel ID and bot token show only for an enabled Slack, and Save
    waits for both while no token is saved (#1578)."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/setup-slack")
    save = page.get_by_role("button", name="Save and continue")
    hint = page.locator("#setup-complete-hint")
    enabled = page.get_by_label("Enable Slack notifications")
    channel = page.get_by_label("Slack channel ID")
    form = page.locator("form.panel")
    hidden(channel)
    ready(save, hint)
    box = spot(enabled)
    enabled.check()
    visible(channel)
    assert spot(enabled) == box
    waits_with(save, hint, SLACK_HINT)
    channel.fill("C0123456789")
    token = page.locator('input[name="candidate"]')
    visible(token)
    waits_with(save, hint, "Paste the Slack bot token to continue.")
    token.fill("xoxb-1-2-fixture")
    ready(save, hint)
    # Unticked: hidden, not sent (so a saved channel clears), Save available.
    enabled.uncheck()
    hidden(channel)
    hidden(token)
    assert spot(enabled) == box
    ready(save, hint)
    sent = form.evaluate("form => Array.from(new FormData(form).keys())")
    assert "channel_id" not in sent and "candidate" not in sent
    # A tick restored from history shows the field again and counts it.
    channel.evaluate("field => { field.disabled = false; field.value = ''; }")
    restore(enabled, "box => { box.checked = true; }")
    visible(channel)
    waits_with(save, hint, SLACK_HINT)
    assert not failures


# Each address field's own words, the shared email rule's (#1577).
TESTING_HINT = "Enter a valid Testing recipient address."
MAILBOX_HINT = "Enter a valid delegated mailbox address."
REPLY_HINT = "Enter a valid reply-to address."


def test_setup_testing_step_waits_for_a_valid_address(page, component_origin):
    """Setup › Testing's Save waits for a valid address, with its hint after
    the action row, so the line coming or going never moves Save (#1332
    item 27). A history restore is worked out again."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/setup-testing")
    save = page.get_by_role("button", name="Save and continue")
    hint = page.locator("#setup-complete-hint")
    address = page.get_by_label("Testing recipient")
    waits_with(save, hint, TESTING_HINT)
    place = spot(save)
    address.fill("not an address")
    waits_with(save, hint, TESTING_HINT)
    # The browser's own email check takes a@b; the server, and so the
    # shared rule, does not.
    address.fill("testing@example")
    waits_with(save, hint, TESTING_HINT)
    address.fill("testing@example.org")
    ready(save, hint)
    assert spot(save) == place
    restore(address, "field => { field.value = ''; }")
    waits_with(save, hint, TESTING_HINT)
    assert not failures


def test_setup_mail_step_waits_for_every_address(page, component_origin):
    """Setup › Outgoing email's Save waits for all three addresses (#1332
    item 27); the optional From name does not hold it."""
    page.goto(component_origin + "/setup-mail")
    save = page.get_by_role("button", name="Save and continue")
    hint = page.locator("#setup-complete-hint")
    waits_with(save, hint, MAILBOX_HINT)
    page.get_by_label("Delegated mailbox").fill("stewardship@example.org")
    page.get_by_label("From address").fill("stewardship@example.org")
    waits_with(save, hint, REPLY_HINT)
    page.get_by_label("Reply-to address").fill("office@example.org")
    ready(save, hint)


LOGO_HINT = "Choose the parish logo image to save."
LOGO_LIMIT = 5 * 1024 * 1024


def logo_file(size):
    """A file for the logo field, ``size`` bytes long (the fixture server
    never reads it; the page checks only its size)."""
    return {"name": "logo.png", "mimeType": "image/png", "buffer": b"\0" * size}


def test_setup_logo_is_refused_at_its_field_when_too_large(page, component_origin):
    """Setup › Parish logos refuses a file over 5 MB at its field as soon as
    it is chosen, in the server's words, and Save waits for a file that fits
    (#1332 item 29). The error line keeps its height, so a message coming
    or going moves nothing; only the shared error border marks the field."""
    from playwright.sync_api import expect

    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/setup-branding")
    save = page.get_by_role("button", name="Save and continue")
    hint = page.locator("#setup-complete-hint")
    field = page.locator("#id_logo")
    error = page.locator("#id_logo_error")
    line = page.locator("#id_logo_error").locator("xpath=..")
    height = line.evaluate("node => node.getBoundingClientRect().height")
    waits_with(save, hint, LOGO_HINT)
    field.set_input_files(logo_file(LOGO_LIMIT + 1))
    visible(error)
    assert error.inner_text().startswith("Parish logo: the file is ")
    assert error.inner_text().endswith("the limit is 5 MB.")
    has_attribute(field, "aria-invalid", "true")
    expect(save).to_be_disabled()
    expect(hint).to_have_text(error.inner_text())
    assert line.evaluate("node => node.getBoundingClientRect().height") == height
    field.set_input_files(logo_file(1024))
    ready(save, hint)
    hidden(error)
    expect(field).not_to_have_attribute("aria-invalid", "true")
    assert line.evaluate("node => node.getBoundingClientRect().height") == height
    assert not failures
