"""The setup wizard runs each test right after its own input step (#1578).

Test email follows Google Workspace connection and Test Slack (when Slack
is on) the one Slack notifications step; Review and Finish setup follow
Load parish data, so Review is the last step before finishing and shows the
tests' results (#1476). A test that was not sent links the steps that fix
it, and saving a fix leads back to the test, never to Review first.
"""

import pytest

from parishkit.stewardship.accounts.setup_wizard import BY_KEY

from .setup_order_components import (
    FAILED,
    FIX,
    RETEST,
    REVIEW_ACCEPTED,
    REVIEW_UNTESTED,
    SLACK_FAILED,
    SLACK_OFF,
    SLACK_STEP,
)
from .test_setup_mail import place
from .waits import has_count, has_text, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

NOTICE = "[data-setup-test-failed]"
REVIEW = '.setup-track a[data-tip*="Review"]'


def track_labels(page):
    """The step bar's step names, in order, from each segment's tooltip."""
    return page.locator(".setup-track a").evaluate_all(
        "links => links.map(link => link.dataset.tip.split(': ')[1].split(' — ')[0])"
    )


@pytest.mark.parametrize("width", [320, 1280])
def test_a_failed_test_links_its_fix_and_the_fix_leads_back_to_the_test(
    page, component_origin, axe_source, width
):
    """The notice says the test was not sent and links the steps that fix
    it, after the action row; following a link and saving the fix lands on
    Test email again, with the old failure gone, and Review still waits."""
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + FAILED)
    notice = page.locator(NOTICE)
    visible(notice)
    assert "The latest test was not sent." in notice.inner_text()
    workspace = notice.get_by_role("link", name="Go to “Google Workspace connection”")
    workspace_url = "/admin/setup/credentials/google_workspace"
    assert workspace.get_attribute("href") == workspace_url
    # The notice follows the action row, so it never moves Back or Send.
    assert place(page, ".setup-actions")[1] < place(page, NOTICE)[1]
    # Back from Test email is the step whose key it proves (#1578).
    back = page.locator(".setup-actions a.setup-back")
    assert back.get_attribute("href") == workspace_url
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    notice.get_by_role("link", name="Go to “Outgoing email settings”").click()
    page.wait_for_url(component_origin + FIX)
    has_text(page.locator("h1"), "Outgoing email settings")
    page.get_by_role("button", name="Save and continue").click()
    page.wait_for_url(component_origin + RETEST)
    has_text(page.locator("h1"), "Test email")
    assert page.locator(NOTICE).count() == 0
    visible(page.get_by_role("button", name="Send test email"))
    assert page.locator(REVIEW).get_attribute("aria-disabled") == "true"


def test_review_is_last_and_shows_the_accepted_test(page, component_origin, axe_source):
    """The step bar runs Google Workspace connection, Test email, and later
    Load parish data, Review, Finish setup (#1578); Review says the test was
    accepted and continues to Finish setup."""
    from .test_components import axe_violations

    page.goto(component_origin + REVIEW_ACCEPTED)
    labels = track_labels(page)
    assert labels[labels.index("Load parish data") :] == [
        "Load parish data",
        "Review",
        "Finish setup",
    ]
    workspace = labels.index("Google Workspace connection")
    assert labels[workspace + 1] == "Test email"
    readiness = page.locator("[data-setup-readiness]")
    has_text(
        readiness.locator("[data-setup-readiness-test]"),
        "Test email: accepted for these settings.",
    )
    assert readiness.get_by_role("link").count() == 0
    finish = page.locator(".setup-actions .setup-continue")
    assert finish.get_attribute("href") == "/admin/setup/confirm"
    assert "Finish setup" in finish.inner_text()
    back = page.locator(".setup-actions a.setup-back")
    assert back.get_attribute("href") == "/admin/setup/source"
    assert axe_violations(page, axe_source) == []


def test_review_before_its_test_links_the_test_and_waits(page, component_origin):
    """Review opened before the email test was accepted names it with a link
    to Test email and offers no Continue to Finish setup."""
    page.goto(component_origin + REVIEW_UNTESTED)
    readiness = page.locator("[data-setup-readiness]")
    test = readiness.locator("[data-setup-readiness-test]")
    assert "Test email: not accepted for these settings yet." in test.inner_text()
    link = readiness.get_by_role("link", name="Go to “Test email”")
    assert link.get_attribute("href") == "/admin/setup/mail-test"
    assert page.locator(".setup-actions .setup-continue").count() == 0


@pytest.mark.parametrize("width", [320, 1280])
def test_a_failed_slack_test_turns_slack_off_and_moves_on(
    page, component_origin, axe_source, width
):
    """A Slack test that was not sent says to use a real bot token or turn
    Slack off, with the button beside it and another above the test form;
    the button posts the Slack step's own form with Slack off, and setup
    lands on the next step not done (here Test email, whose test was not
    sent) with Test Slack gone from the step bar (#1499). There is no Slack
    connection step (#1578)."""
    from urllib.parse import parse_qs

    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + SLACK_FAILED)
    notice = page.locator(NOTICE)
    visible(notice)
    assert "Use a real Slack bot token" in notice.inner_text()
    labels = track_labels(page)
    assert "Test Slack" in labels and "Slack connection" not in labels
    # Test Slack follows the Slack step it proves.
    assert (
        labels.index("Test Slack") == labels.index("Slack notifications (optional)") + 1
    )
    buttons = page.get_by_role("button", name="Turn off Slack notifications")
    has_count(buttons, 2)
    # One is above the test form, so it is reached without scrolling past
    # it; the notice's follows the action row, so neither moves Send.
    assert place(page, "[data-setup-slack-off]")[1] < place(page, "#setup-test")[1]
    assert place(page, ".setup-actions")[1] < place(page, NOTICE)[1]
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []
    with page.expect_request(
        lambda request: request.method == "POST" and request.url.endswith(SLACK_OFF)
    ) as posted:
        notice.get_by_role("button", name="Turn off Slack notifications").click()
    fields = parse_qs(posted.value.post_data, keep_blank_values=True)
    assert fields["channel_id"] == [""] and fields["version"] == ["2"]
    assert "enabled" not in fields
    page.wait_for_url(component_origin + RETEST)
    has_text(page.locator("h1"), "Test email")
    labels = track_labels(page)
    assert "Slack notifications (optional)" in labels
    assert "Test Slack" not in labels


@pytest.mark.parametrize("width", [320, 1280])
def test_the_slack_step_takes_the_token_and_offers_turning_slack_off(
    page, component_origin, axe_source, width
):
    """The one Slack step (#1578) shows the channel and bot token below
    Enable Slack notifications while it is ticked. A saved token is kept by
    leaving the field empty, unless the channel changes, when the token is
    needed again; unticking hides both without moving the switch. Turn off
    Slack notifications sits above the form and leads on as on Test Slack
    (#1499)."""
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    page.goto(component_origin + SLACK_STEP)
    has_text(page.locator("h1"), "Slack notifications (optional)")
    off = page.locator("[data-setup-slack-off]")
    visible(off)
    assert "Slack is optional." in off.inner_text()
    notice = page.locator(".notice", has=off).inner_text()
    assert "checks only that the token looks like a Slack bot token" in notice
    assert place(page, "[data-setup-slack-off]")[1] < place(page, "form.panel")[1]
    has_text(
        page.locator(".setup-saved strong"),
        "A Slack bot token is saved for this channel.",
    )
    switch = page.get_by_label("Enable Slack notifications")
    channel = page.get_by_label("Slack channel ID")
    token = page.locator('input[name="candidate"]')
    save = page.get_by_role("button", name="Save and continue")
    visible(channel)
    visible(token)
    assert token.get_attribute("type") == "password"
    # The saved token is kept for its own channel: Save is available empty.
    assert not token.evaluate("node => node.required")
    assert save.is_enabled()
    # A new channel needs the token again, and Save waits for it.
    channel.fill("C0NEWCHANNEL")
    channel.dispatch_event("input")
    assert token.evaluate("node => node.required")
    assert save.is_disabled()
    token.fill("xoxb-1-2-fixture")
    token.dispatch_event("input")
    assert save.is_enabled()
    assert axe_violations(page, axe_source) == []
    # Unticking hides the channel and token, and the switch stays put.
    before = place(page, "#" + switch.get_attribute("id"))
    switch.uncheck()
    hidden(channel)
    hidden(token)
    assert token.is_disabled()
    assert same_place(place(page, "#" + switch.get_attribute("id")), before)
    assert save.is_enabled()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    off.get_by_role("button", name="Turn off Slack notifications").click()
    page.wait_for_url(component_origin + RETEST)
    assert "Test Slack" not in track_labels(page)


def test_back_to_test_slack_after_turning_slack_off_opens_test_email(
    page, component_origin
):
    """Going back to Test Slack after Turn off Slack notifications opens the
    next step needing work, Test email, not an error page (#1505).

    The Test Slack page is served at its real address the first time and,
    from then on, the address answers as the real view does while Slack is
    off (a redirect). The turn-off form posts to the Slack notifications
    step's own address, a different one, so every engine keeps Test Slack
    as a real history entry, and Back fetches it again: it is marked
    no-store, as the real page is.
    """
    test_slack = component_origin + BY_KEY["slack_test"].url
    served = []

    def first_page_then_server(route):
        """Serve the Test Slack page once, then let the server answer."""
        served.append(route.request.url)
        if len(served) > 1:
            route.continue_()
            return
        answer = route.fetch(url=component_origin + SLACK_FAILED)
        headers = answer.headers | {"cache-control": "no-store"}
        route.fulfill(response=answer, headers=headers)

    page.route(test_slack, first_page_then_server)
    page.goto(test_slack)
    has_text(page.locator("h1"), "Test Slack")
    off = page.locator("[data-setup-slack-off]").first
    off.get_by_role("button", name="Turn off Slack notifications").click()
    page.wait_for_url(component_origin + RETEST)
    with page.expect_response(test_slack) as back:
        page.go_back()
    assert back.value.status == 302
    page.wait_for_url(component_origin + RETEST)
    has_text(page.locator("h1"), "Test email")
    assert len(served) == 2
    assert "Test Slack" not in track_labels(page)
