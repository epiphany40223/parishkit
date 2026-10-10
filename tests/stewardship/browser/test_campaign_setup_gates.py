"""Campaign and Slack setup forms wait for what the server needs (#563).

Campaign settings and Setup › First campaign keep their action unavailable
until a campaign module is ticked and, with Financial stewardship ticked,
both financial periods and their funds are filled in and a shown overlap is
confirmed. Campaign settings' Review also waits for a change from the saved
settings (#921), so its tests make a real edit before the complete gate is
the one deciding, and one test checks the two gates together. Setup › Slack
shows the channel ID only while Slack is enabled, and then waits for it.
Each hint sits after its button, so a line coming or going never moves the
control just clicked, or the button. Every state is
worked out again on pageshow, when a page restored from history gets back its
values without change events.
"""

import pytest

from .waits import hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
MODULE_HINT = (
    "Choose at least one: Census, Ministry stewardship or Financial stewardship."
)
FINANCIAL_HINT = "Fill in both financial periods and their funds."
OVERLAP_HINT = "Confirm the overlap with the campaign dates, or change the dates."
SLACK_HINT = "Enter the Slack channel ID, or untick Enable Slack notifications."
UNCHANGED_HINT = "Change a setting to review it."


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
    """Assert the button is unavailable and its linked hint says ``text``."""
    assert button.is_disabled()
    visible(hint)
    assert hint.text_content() == text
    assert hint.get_attribute("id") in button.get_attribute("aria-describedby").split()


def ready(button, hint):
    """Assert the button is available and its hint is gone."""
    assert button.is_enabled()
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
    field, fund and a shown overlap confirmation; nothing clicked moves."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/campaign-settings")
    review = page.get_by_role("button", name="Review changes")
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
    visible(page.get_by_role("group", name="Financial periods and funds"))
    waits_with(review, hint, FINANCIAL_HINT)
    page.get_by_label("Upcoming financial period start").fill("2027-01-01")
    page.get_by_label("Comparison financial period start").fill("2025-01-01")
    page.get_by_label("Upcoming financial period funds").select_option("9")
    assert review.is_disabled()
    page.get_by_label("Comparison financial period funds").select_option("9")
    ready(review, hint)
    # A period overlapping the campaign (October 2026) asks for confirmation.
    page.get_by_label("Upcoming financial period start").fill("2026-01-01")
    confirm = page.get_by_label("I confirm that the upcoming financial period")
    visible(confirm)
    waits_with(review, hint, OVERLAP_HINT)
    confirm.check()
    ready(review, hint)
    # Unticking Financial stewardship hides and drops every financial field.
    page.get_by_label("Upcoming financial period start").fill("")
    assert review.is_disabled()
    financial.uncheck()
    # Its disabled fields no longer count, and the renamed campaign is still
    # a change, so Review is available again.
    ready(review, hint)
    assert not failures


def test_campaign_settings_rechecks_restored_modules(page, component_origin):
    """A module tick restored from history, without an event, is counted."""
    page.goto(component_origin + "/campaign-settings")
    review = page.get_by_role("button", name="Review changes")
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
    review = page.get_by_role("button", name="Review changes")
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


def test_first_campaign_waits_for_a_module(page, component_origin):
    """Setup's First campaign shares the gate, with its hint below Save."""
    failures = []
    page.on("pageerror", lambda error: failures.append(str(error)))
    page.goto(component_origin + "/setup-campaign")
    save = page.get_by_role("button", name="Save and continue")
    hint = page.locator("#setup-complete-hint")
    page.get_by_label("Campaign name").fill("Sample campaign")
    page.get_by_label("Campaign start date").fill("2026-10-01")
    page.get_by_label("Campaign end date").fill("2026-10-31")
    waits_with(save, hint, MODULE_HINT)
    ministry = page.locator("#id_ministry")
    where = (spot(ministry), spot(save))
    ministry.check()
    ready(save, hint)
    # Only Ministry selections opened, below the box: the box stays put.
    assert spot(ministry) == where[0]
    restore(ministry, "box => { box.checked = false; }")
    waits_with(save, hint, MODULE_HINT)
    assert not failures


def test_slack_channel_shows_and_is_required_only_while_enabled(page, component_origin):
    """The channel ID shows only for an enabled Slack, and Save waits for it."""
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
    ready(save, hint)
    # Unticked: hidden, not sent (so a saved channel clears), Save available.
    enabled.uncheck()
    hidden(channel)
    assert spot(enabled) == box
    ready(save, hint)
    sent = form.evaluate("form => Array.from(new FormData(form).keys())")
    assert "channel_id" not in sent
    # A tick restored from history shows the field again and counts it.
    channel.evaluate("field => { field.disabled = false; field.value = ''; }")
    restore(enabled, "box => { box.checked = true; }")
    visible(channel)
    waits_with(save, hint, SLACK_HINT)
    assert not failures
