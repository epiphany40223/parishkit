"""Campaign and Slack setup forms wait for what the server needs (#563).

Campaign settings and Setup › First campaign keep their action unavailable
until a campaign module is ticked and, with Financial stewardship ticked,
both financial periods and their funds are filled in and a shown overlap is
confirmed. Setup › Slack shows the channel ID only while Slack is enabled,
and then waits for it. Each hint sits after its button, so a line coming or
going never moves the control just clicked, or the button. Every state is
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
    ready(review, hint)
    assert not failures


def test_campaign_settings_rechecks_restored_modules(page, component_origin):
    """A module tick restored from history, without an event, is counted."""
    page.goto(component_origin + "/campaign-settings")
    review = page.get_by_role("button", name="Review changes")
    hint = page.locator("#settings-complete-hint")
    restore(page.locator("#id_census"), "box => { box.checked = false; }")
    waits_with(review, hint, MODULE_HINT)
    # Financial stewardship restored with its fields still empty.
    restore(page.locator("#id_financial_enabled"), "box => { box.checked = true; }")
    waits_with(review, hint, FINANCIAL_HINT)
    restore(page.locator("#id_financial_enabled"), "box => { box.checked = false; }")
    waits_with(review, hint, MODULE_HINT)
    restore(page.locator("#id_census"), "box => { box.checked = true; }")
    ready(review, hint)


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
