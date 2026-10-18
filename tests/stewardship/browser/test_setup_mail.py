"""The setup test pages follow a test in place (#1354).

Test email and Test Slack watch the passive status fragment while a test is
queued or being sent (live-status-v1.js), and once it settles re-read their
panel in place, so the acknowledgement box, Send another test and Continue
come from the server. Each test marks the page (a mark only a full load
could lose) and checks that it stayed.
"""

from datetime import timedelta

import pytest

from .conftest import NOW, SETUP_MAIL_STATUS, SETUP_SLACK_STATUS
from .setup_live_components import (
    ACCEPTED_ID,
    QUEUED_ID,
    SEND,
    SENT,
    SETTLED,
    mail_status,
    status_row,
)
from .test_in_place import MARK, MARKED
from .test_live_status_in_place import answer_reads
from .waits import focused, has_text, hidden, recorded, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

STATUS = {"mail": SETUP_MAIL_STATUS, "slack": SETUP_SLACK_STATUS}


def status_route(page, channel, answers):
    """Answer each status poll with the next of ``answers`` (fragment, or an
    HTTP status number), the last one from then on; return the polls."""
    polls = []

    def respond(route):
        """Record the poll and answer it without sending anything."""
        polls.append(route.request)
        answer = answers[min(len(polls), len(answers)) - 1]
        if isinstance(answer, int):
            route.fulfill(status=answer, body="")
        else:
            route.fulfill(content_type="text/html", body=answer)

    page.route(lambda url: url.endswith(STATUS[channel]), respond)
    return polls


def place(page, selector):
    """Where ``selector`` is drawn on the page (not in the viewport, which
    focus may scroll), to check that nothing above a change moved."""
    return page.locator(selector).first.evaluate(
        "node => { const box = node.getBoundingClientRect();"
        " return [box.left + scrollX, box.top + scrollY, box.width, box.height]; }"
    )


@pytest.fixture(params=["mail", "slack"])
def delivery_channel(request):
    """Both readiness controls use the same passive, bounded browser contract."""
    return request.param


@pytest.mark.parametrize("state", ["accepted", "delivery_unknown"])
def test_settled_test_updates_the_page_in_place_and_stops(
    page, component_origin, axe_source, state, delivery_channel
):
    """A queued test settles without a reload: the list, then the panel, update
    in place, focus and the page above stay put, and polling stops."""
    page.clock.install(time=NOW)
    pending = f"/setup-{delivery_channel}-test"
    queued = status_row(QUEUED_ID, "queued", NOW)
    settled = status_row(QUEUED_ID, state, NOW)
    polls = status_route(
        page,
        delivery_channel,
        [mail_status([queued], pending=True), mail_status([settled], pending=False)],
    )
    reads = answer_reads(
        page, component_origin, pending, SETTLED[(delivery_channel, state)]
    )
    page.goto(component_origin + pending)
    page.evaluate(MARK)
    send = page.locator("[data-mail-send]").first
    assert send.is_disabled()
    heading = page.locator("#setup-tests-heading")
    heading.focus()
    above = place(page, ".panel h2")
    # The first poll still finds the test queued; the next one, settled.
    page.clock.fast_forward(2000)
    page.clock.fast_forward(3000)
    checkbox = page.locator("[data-mail-uncertain] input")
    if state == "accepted":
        visible(page.locator(".setup-actions").get_by_role("link", name="Continue"))
        hidden(checkbox)
    else:
        visible(checkbox)
    assert len(reads) == 1
    assert page.evaluate(MARKED) == "kept"
    focused(heading)
    assert same_place(place(page, ".panel h2"), above)
    if state == "delivery_unknown":
        assert checkbox.evaluate("node => node.required")
        # The acknowledgement holds Send until it is ticked.
        assert page.locator("[data-mail-send]").first.is_disabled()
        checkbox.check()
        assert page.locator("[data-mail-send]").first.is_enabled()
    has_text(page.locator("[data-live-announcer]"), f"Latest test: {settled['label']}.")
    # Every poll is a passive GET with no body; none follows the settled one.
    assert len(polls) == 2
    assert all(poll.method == "GET" and poll.post_data is None for poll in polls)
    page.clock.fast_forward(60000)
    page.wait_for_timeout(200)
    assert len(polls) == 2
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    from .test_components import axe_violations

    assert axe_violations(page, axe_source) == []
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


@pytest.mark.parametrize("width", [320, 1280])
def test_send_another_test_is_in_place(page, component_origin, axe_source, width):
    """Send another test posts in place: the queued test appears without a
    reload, focus lands on Recent tests, and the page watches it to the end."""
    from playwright.sync_api import expect

    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    page.clock.install(time=NOW)
    later = NOW + timedelta(minutes=1)
    queued = status_row(QUEUED_ID, "queued", later)
    accepted = status_row(ACCEPTED_ID, "accepted", NOW)
    polls = status_route(
        page,
        "mail",
        [
            mail_status([queued, accepted], pending=True),
            mail_status(
                [status_row(QUEUED_ID, "accepted", later), accepted], pending=False
            ),
        ],
    )
    reads = answer_reads(page, component_origin, SENT, SEND)
    page.goto(component_origin + SEND)
    page.evaluate(MARK)
    above = place(page, "h1")
    page.get_by_role("button", name="Send another test").click()
    expect(page.locator("[data-mail-id]").first).to_contain_text("Still sending")
    assert page.evaluate(MARKED) == "kept"
    focused(page.locator("#setup-tests-heading"))
    assert page.get_by_role("button", name="Send another test").is_disabled()
    assert same_place(place(page, "h1"), above)
    assert axe_violations(page, axe_source) == []
    # The first poll still finds the test queued; the next one, settled.
    page.clock.fast_forward(2000)
    page.clock.fast_forward(3000)
    # Settled: the panel is read again in place and Send is offered again.
    recorded(page, reads, 1)
    assert len(polls) == 2
    visible(page.get_by_role("button", name="Send another test"))
    assert page.get_by_role("button", name="Send another test").is_enabled()
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


def test_status_error_retries_and_refusal_stops(page, component_origin):
    """A failed check says so and retries; a refused one stops watching."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    queued = mail_status([status_row(QUEUED_ID, "queued", NOW)], pending=True)
    polls = status_route(page, "mail", [500, queued, 403])
    page.goto(component_origin + "/setup-mail-test")
    page.evaluate(MARK)
    page.clock.fast_forward(2000)
    recorded(page, polls, 1)
    problem = page.locator(".live-status-problem")
    expect(problem).to_contain_text("retrying")
    page.clock.fast_forward(2000)
    recorded(page, polls, 2)
    hidden(problem)
    page.clock.fast_forward(3000)
    recorded(page, polls, 3)
    expect(problem).to_contain_text("Couldn't check for updates")
    page.clock.fast_forward(60000)
    page.wait_for_timeout(200)
    assert len(polls) == 3
    assert page.evaluate(MARKED) == "kept"
    # Nothing could have been sent while the result was unknown.
    assert page.locator("[data-mail-send]").first.is_disabled()


def test_bad_request_refusal_stops_watching(page, component_origin, delivery_channel):
    """A 400 (such as Slack turned off) is a refusal like a 403: no retry."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    queued = mail_status([status_row(QUEUED_ID, "queued", NOW)], pending=True)
    polls = status_route(page, delivery_channel, [queued, 400])
    page.goto(component_origin + f"/setup-{delivery_channel}-test")
    page.clock.fast_forward(2000)
    recorded(page, polls, 1)
    page.clock.fast_forward(2000)
    recorded(page, polls, 2)
    expect(page.locator(".live-status-problem")).to_contain_text("Couldn't check")
    page.clock.fast_forward(60000)
    page.wait_for_timeout(200)
    assert len(polls) == 2


def test_accepted_email_test_offers_continue_and_another_send(page, component_origin):
    """After acceptance the primary action is Continue; sending again is secondary."""
    page.goto(component_origin + "/setup-mail-test-done")
    row = page.locator(".setup-actions")
    visible(row.get_by_role("link", name="Continue"))
    assert row.locator("button").count() == 0
    visible(page.get_by_role("button", name="Send another test"))


def test_restored_settled_page_is_read_again(page, component_origin):
    """A settled page restored from the back/forward cache (pageshow,
    persisted) re-reads its panel, so Continue gives way to what the server
    now says. A pageshow that is not a restore reads nothing."""
    address = "/setup-mail-test-done"
    reads = answer_reads(
        page, component_origin, address, SETTLED[("mail", "delivery_unknown")]
    )
    page.goto(component_origin + address)
    page.evaluate(MARK)
    visible(page.locator(".setup-actions").get_by_role("link", name="Continue"))
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow'))")
    page.wait_for_timeout(200)
    assert not reads
    page.evaluate(
        "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
    )
    visible(page.locator("[data-mail-uncertain] input"))
    hidden(page.locator(".setup-actions").get_by_role("link", name="Continue"))
    assert len(reads) == 1
    assert page.evaluate(MARKED) == "kept"


def named(button):
    """The ids ``button`` names (aria-describedby)."""
    return (button.get_attribute("aria-describedby") or "").split()


def test_held_send_names_the_pending_test_and_a_failed_check(
    page, component_origin, delivery_channel
):
    """While a test is pending, Send is unavailable and names the pending
    line below the action row; while the status cannot be checked, it names
    that line too, and stops once a check succeeds (#1332 item 28)."""
    from playwright.sync_api import expect

    page.clock.install(time=NOW)
    queued = mail_status([status_row(QUEUED_ID, "queued", NOW)], pending=True)
    polls = status_route(page, delivery_channel, [500, queued, 403])
    page.goto(component_origin + f"/setup-{delivery_channel}-test")
    page.evaluate(MARK)
    send = page.locator("[data-mail-send]").first
    pending = page.locator("#setup-test-pending")
    assert send.is_disabled()
    visible(pending)
    has_text(
        pending,
        "A test is on its way. Another test can be sent once its result is known.",
    )
    assert named(send) == ["setup-test-pending"]
    # A failed check: its line has an id, and Send names it as well.
    page.clock.fast_forward(2000)
    recorded(page, polls, 1)
    problem = page.locator("#setup-test-problem")
    expect(problem).to_contain_text("retrying")
    assert named(send) == ["setup-test-pending", "setup-test-problem"]
    # A successful check clears the line and Send's mention of it.
    page.clock.fast_forward(2000)
    recorded(page, polls, 2)
    hidden(problem)
    assert named(send) == ["setup-test-pending"]
    # A refused check stops watching; Send names why it still waits.
    page.clock.fast_forward(3000)
    recorded(page, polls, 3)
    expect(problem).to_contain_text("Couldn't check for updates")
    assert named(send) == ["setup-test-pending", "setup-test-problem"]
    assert send.is_disabled()
    assert page.locator("#setup-test-problem").count() == 1
    assert page.evaluate(MARKED) == "kept"
