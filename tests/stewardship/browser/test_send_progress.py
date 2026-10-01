"""Family email progress in a real browser (#413): accessible, live and quiet."""

from itertools import pairwise
from time import monotonic

import pytest

from .send_progress_components import FINISHED, PAGE, STATUS, status
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
REGION = '[data-live-status="send-progress"]'
ANNOUNCER = '[data-live-announcer="send-progress"]'


@pytest.mark.parametrize("width", [320, 1280])
def test_progress_pages_are_accessible_at_phone_and_desktop_widths(
    page, component_origin, axe_source, width
):
    """Running and finished pages pass axe, fit the phone, and label the bar."""
    page.set_viewport_size({"width": width, "height": 900})
    for path in (PAGE, FINISHED):
        page.goto(component_origin + path)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.evaluate(axe_source)
        assert (
            page.evaluate("""async () => (await axe.run(document, {
            runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa','wcag22aa']}
        })).violations.map(({id,impact}) => ({id,impact}))""")
            == []
        )
        bar = page.get_by_role("progressbar")
        assert bar.count() == 1
        name = page.evaluate(
            "document.getElementById("
            "document.querySelector('progress').getAttribute('aria-labelledby')"
            ").textContent"
        )
        assert "emails finished" in name
        # Counts change on every poll, so the region itself never speaks.
        assert page.locator(REGION).get_attribute("aria-live") is None
        assert page.get_by_role("link", name="Outgoing mail").count() >= 1


def test_the_running_page_follows_the_send_and_announces_only_its_end(
    page, component_origin
):
    """One poll swaps in the finished counts; the announcer says so once."""
    page.goto(component_origin + PAGE)
    region = page.locator(REGION)
    assert region.get_attribute("data-live-pending") is not None
    assert region.get_attribute("data-live-interval") == "5000"
    visible(page.get_by_text("500 of 1,100 emails finished (45%)"))
    # The first rendering is not announced; nothing has changed yet.
    assert page.locator(ANNOUNCER).text_content() == ""
    # The fixture's status fragment reports the send as finished.
    from playwright.sync_api import expect

    # The page polls every 5 s; allow a few polls on a slow engine.
    expect(page.get_by_text("Finished: no emails remain.")).to_be_visible(timeout=20000)
    assert region.get_attribute("data-live-pending") is None
    visible(page.get_by_text("1,100 of 1,100 emails finished (100%)"))
    assert page.locator(ANNOUNCER).text_content() == "Family email send finished."
    # Technical details stay outside the polled region and survive the swap.
    assert page.locator(REGION + " details").count() == 0
    assert page.locator("details.technical-details").count() == 1


def test_polls_every_five_seconds_and_announces_only_quarter_changes(
    page, component_origin
):
    """A count change inside a quarter is silent; a new quarter speaks once."""
    from playwright.sync_api import expect

    from .conftest import NOW

    bodies = [
        # 530 of 1,100 (48%): new counts, same quarter as the page's 45%.
        status(NOW, sent=510, remaining=570),
        # 620 of 1,100 (56%): the 50% quarter.
        status(NOW, sent=600, remaining=480),
        status(NOW, sent=1080, remaining=0, recent=0, last_settled_at=NOW),
    ]
    polls = []

    def respond(route):
        """Answer each poll with the next state, recording when it came."""
        polls.append(monotonic())
        route.fulfill(content_type="text/html", body=bodies[min(len(polls), 3) - 1])

    page.route("**" + STATUS, respond)
    page.goto(component_origin + PAGE)
    announcer = page.locator(ANNOUNCER)
    expect(page.get_by_text("530 of 1,100 emails finished (48%)")).to_be_visible(
        timeout=10000
    )
    assert announcer.text_content() == ""
    expect(page.get_by_text("620 of 1,100 emails finished (56%)")).to_be_visible(
        timeout=10000
    )
    expect(announcer).to_have_text("Family email send in progress, 50% done.")
    expect(page.get_by_text("Finished: no emails remain.")).to_be_visible(timeout=10000)
    expect(announcer).to_have_text("Family email send finished.")
    # A fixed 5-second pace, not the usual 2-second start and back-off.
    gaps = [later - earlier for earlier, later in pairwise(polls)]
    assert len(polls) == 3 and all(4.5 <= gap < 8 for gap in gaps), gaps


def test_watching_outlasts_the_old_hour_while_the_send_progresses(
    page, component_origin
):
    """The limit counts from the last change, so a long watch keeps going.

    A page opened up to an hour before a send must follow it to the end.
    Each poll that brings new counts restarts the 3-hour limit; once the
    counts stop changing for that long, watching stops and the message
    names this page's own refresh link.
    """
    from playwright.sync_api import expect

    from .conftest import NOW
    from .waits import recorded

    polls = []

    def respond(route):
        """New counts on the first three polls, then the same ones."""
        polls.append(monotonic())
        step = min(len(polls), 3)
        body = status(NOW, sent=480 + 10 * step, remaining=600 - 10 * step)
        route.fulfill(content_type="text/html", body=body)

    page.clock.install(time=NOW)
    page.route("**" + STATUS, respond)
    page.goto(component_origin + PAGE)
    problem = page.locator(".live-status-problem")
    for count in (1, 2, 3):
        # Two hours after load in all, well past the old one-hour limit.
        page.clock.fast_forward(40 * 60 * 1000)
        recorded(page, polls, count)
    expect(page.get_by_text("530 of 1,100 emails finished (48%)")).to_be_visible()
    assert problem.is_hidden()
    # Unchanged counts no longer renew the limit: 40 + 150 minutes later the
    # page stops watching.
    page.clock.fast_forward(40 * 60 * 1000)
    recorded(page, polls, 4)
    page.clock.fast_forward(150 * 60 * 1000)
    recorded(page, polls, 5)
    expect(problem).to_have_text("Still working. Use Refresh progress to check again.")
    page.clock.fast_forward(60 * 60 * 1000)
    page.wait_for_timeout(200)
    assert len(polls) == 5
