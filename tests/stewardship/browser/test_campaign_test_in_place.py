"""Preview and test email's Send this test email acts in place (#519).

The page is served at its real address (campaign_test_components), and the
fixture server answers Send with its view's 303 back to the page. The test
sets a mark outside every region, which only a full page load could lose,
wheels the page so Send is below the fold, and checks the mark, that the
page did not jump back to the top, where focus went, the announcement, the
pending list, that it settles by itself (live-status-v1.js) with focus kept,
that Send comes back, and that exactly one POST was sent. A refused Send
(409) is shown whole and sent once.
"""

from uuid import UUID

import pytest

from .campaign_test_components import (
    PAGE,
    REFUSED,
    SENT,
    SETTLED,
    WIZARD,
    WIZARD_SENT,
    WIZARD_SETTLED,
)
from .test_export_resolve_in_place import announced, open_marked, stayed, wheel_to
from .test_in_place import MARKED, count_requests
from .waits import focused, painted, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

NAME = "Send this test email"


def test_send_shows_the_test_and_its_result_in_place(page, component_origin):
    """Send posts once (a double click included) and follows the view's
    redirect back to the page: Recent test results shows the new test as
    pending and its heading takes focus. The list then settles by itself,
    and its quiet follow-up re-reads the form's region, so Send is offered
    again, all without a reload, a jump to the top or a focus move."""
    from playwright.sync_api import expect  # imported late: collection needs no browser

    open_marked(page, component_origin, PAGE)
    assert page.get_by_role("link", name="Refresh test results").count() == 0
    # The pending page's polls (its data-live-url, SENT) are answered with
    # the settled list; the redirect Send follows is served as is.
    polls = []

    def settle(route):
        """Answer a poll of the pending page with the settled page; the
        redirect Send follows (ui-v1.js marks its requests) goes through."""
        if route.request.headers.get("x-requested-with") == "fetch":
            route.continue_()
            return
        polls.append(route.request.url)
        route.fulfill(response=route.fetch(url=component_origin + SETTLED))

    page.route(lambda url: url.split("#")[0] == component_origin + SENT, settle)
    posts = count_requests(page, "POST", PAGE)
    button = page.get_by_role("button", name=NAME, exact=True)
    offset = wheel_to(page, button)
    button.dblclick()
    announced(page, "Test email requested.")
    stayed(page, offset)
    heading = page.get_by_role("heading", name="Recent test results")
    focused(heading)
    visible(page.locator("#campaign-test-pending"))
    assert page.get_by_role("button", name=NAME, exact=True).is_disabled()
    assert page.url == component_origin + SENT + "#campaign-tests"
    # live-status-v1.js polls after its first delay (2 s), then the settled
    # list's follow-up re-reads the bare page's form region.
    expect(page.locator("#campaign-tests li").first).to_contain_text(
        "Delivered", timeout=10000
    )
    expect(page.locator("#campaign-test-pending")).to_have_count(0, timeout=10000)
    expect(page.get_by_role("button", name=NAME, exact=True)).to_be_enabled()
    focused(heading)
    assert polls
    assert posts == [component_origin + PAGE]
    assert page.evaluate(MARKED) == "kept"
    # Technical details sit after the polled list, never inside it (a poll
    # would close them), and list each test's reference as Send drew them.
    details = page.locator("#campaign-tests details.technical-details")
    expect(details).to_have_count(1)
    assert page.locator("[data-live-status] .technical-details").count() == 0
    details.locator("summary").click()
    for number in (5411, 5410):
        expect(details.locator("code", has_text=str(UUID(int=number)))).to_be_visible()
    expect(details).to_contain_text(
        "As of when this page was opened; reload the page to update."
    )


def test_first_settled_test_shows_continue_in_place(page, component_origin):
    """In Campaign setup, one Send is enough (#1583): Send's own answer is
    drawn while the test is queued, so the step is not done and there is no
    Continue yet. Once the test is accepted, the settled list's quiet
    follow-up redraws the stepper and the action row too, so the step reads
    Completed and Continue appears next to Back, without a reload, a second
    POST or the action row moving."""
    from playwright.sync_api import expect  # imported late: collection needs no browser

    open_marked(page, component_origin, WIZARD)

    def settle(route):
        """Answer a poll of the pending page with the settled page; the
        redirect Send follows (ui-v1.js marks its requests) goes through."""
        if route.request.headers.get("x-requested-with") == "fetch":
            route.continue_()
            return
        route.fulfill(response=route.fetch(url=component_origin + WIZARD_SETTLED))

    page.route(lambda url: url.split("#")[0] == component_origin + WIZARD_SENT, settle)
    posts = count_requests(page, "POST", WIZARD)
    actions = page.locator("#campaign-step-actions")
    back = actions.get_by_role("link", name="Back")
    continuing = actions.locator("[data-campaign-continue]")
    expect(continuing).to_have_count(0)
    count = page.locator("#campaign-stepper .setup-stepper-count")
    before = count.text_content()
    button = page.get_by_role("button", name=NAME, exact=True)
    button.click()
    announced(page, "Test email requested.")
    visible(page.locator("#campaign-test-pending"))
    # Send's answer: the test is queued, so the step is not done yet.
    expect(continuing).to_have_count(0)
    # Where Back sits in the row, and the row's height: the pending notice
    # above leaves as the test settles, so the row as a whole may move up.
    row = actions.bounding_box()
    spot = (back.bounding_box()["y"] - row["y"], row["height"])
    expect(page.locator("#campaign-test-pending")).to_have_count(0, timeout=10000)
    expect(continuing).to_have_text("Continue to Go-live readiness", timeout=10000)
    # The step now counts as completed (one more than before).
    expect(count).not_to_have_text(before)
    # Continue joins Back in the row: the row keeps its height and Back
    # stays where it was in it.
    row = actions.bounding_box()
    assert (back.bounding_box()["y"] - row["y"], row["height"]) == spot
    assert posts == [component_origin + WIZARD]
    assert page.evaluate(MARKED) == "kept"


def test_refused_send_is_shown_once(page, component_origin):
    """A refused Send (the view's 409 error page, which draws neither of
    the page's regions) is shown whole, as a native submission shows it,
    never swallowed, and is not sent again."""
    open_marked(page, component_origin, REFUSED)
    posts = count_requests(page, "POST", REFUSED)
    button = page.get_by_role("button", name=NAME, exact=True)
    wheel_to(page, button)
    button.click()
    visible(page.get_by_role("heading", name="This page is out of date"))
    painted(page)
    assert posts == [component_origin + REFUSED]
