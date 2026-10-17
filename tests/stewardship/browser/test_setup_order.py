"""The setup wizard runs its tests before Review (#1476).

After Load parish data come Test email, Test Slack (when Slack is on),
Review and Finish setup, so Review is the last step before finishing and
shows the tests' results. A test that was not sent links the steps that fix
it, and saving a fix leads back to the test, never to Review first.
"""

import pytest

from .setup_order_components import (
    FAILED,
    FIX,
    RETEST,
    REVIEW_ACCEPTED,
    REVIEW_UNTESTED,
)
from .test_setup_mail import place
from .waits import has_text, visible

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
    back = page.locator(".setup-actions a.setup-back")
    assert back.get_attribute("href") == "/admin/setup/source"
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
    """The step bar runs Load parish data, Test email, Review, Finish setup;
    Review says the test was accepted and continues to Finish setup."""
    from .test_components import axe_violations

    page.goto(component_origin + REVIEW_ACCEPTED)
    labels = track_labels(page)
    assert labels[labels.index("Load parish data") :] == [
        "Load parish data",
        "Test email",
        "Review",
        "Finish setup",
    ]
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
    assert back.get_attribute("href") == "/admin/setup/mail-test"
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
