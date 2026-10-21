"""The Mail queue in a real browser (#1562, #413): accessible, live and quiet."""

from datetime import timedelta
from itertools import pairwise
from time import monotonic

import pytest

from .send_progress_components import FINISHED, PAGE, STATUS, status
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)
REGION = '[data-live-status="mail-queue"]'
ANNOUNCER = '[data-live-announcer="mail-queue"]'


@pytest.mark.parametrize("width", [320, 1280])
def test_progress_pages_are_accessible_at_phone_and_desktop_widths(
    page, component_origin, axe_source, width
):
    """Running and idle pages pass axe and fit the phone; only running has a bar."""
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
        if path == FINISHED:
            # No send in progress: a one-line summary, no bar.
            assert bar.count() == 0
            visible(page.get_by_text("No Family email send is in progress right now"))
        else:
            assert bar.count() == 1
            name = page.evaluate(
                "document.getElementById("
                "document.querySelector('progress').getAttribute('aria-labelledby')"
                ").textContent"
            )
            assert "emails finished" in name
        # Counts change on every poll, so the region itself never speaks.
        assert page.locator(REGION).get_attribute("aria-live") is None
        assert page.get_by_role("link", name="Sent mail").count() >= 1
        # Four headline tiles and one row per kind Sent mail lists.
        tiles = page.get_by_role("list", name="Emails waiting to go out")
        assert tiles.get_by_role("listitem").count() == 4
        table = page.get_by_role("table", name="Queued emails by kind")
        assert table.locator("tbody tr").count() >= 8


def test_the_running_page_follows_the_send_and_announces_only_its_end(
    page, component_origin
):
    """One poll swaps in the idle state; the announcer says so once.

    The page keeps checking after the send ends, so the next one appears.
    """
    page.goto(component_origin + PAGE)
    region = page.locator(REGION)
    assert region.get_attribute("data-live-pending") is not None
    assert region.get_attribute("data-live-interval") == "5000"
    visible(page.get_by_text("500 of 1,100 emails finished (45%)"))
    visible(page.get_by_role("link", name="1 email queued in Testing"))
    kinds = page.get_by_role("table", name="Queued emails by kind")
    top = kinds.bounding_box()["y"]
    visible(kinds.get_by_role("link", name="Initial invitation"))
    # The first rendering is not announced; nothing has changed yet.
    assert page.locator(ANNOUNCER).text_content() == ""
    # The fixture's status fragment reports the send as finished.
    from playwright.sync_api import expect

    # The page polls every 5 s; allow a few polls on a slow engine.
    expect(
        page.get_by_text("No Family email send is in progress right now")
    ).to_be_visible(timeout=20000)
    assert region.get_attribute("data-live-pending") is not None
    visible(page.get_by_text("Last send: Invitation email"))
    # The queue emptied: the other mode's line and the Kind links went, and
    # nothing moved (no layout shift): the line kept its height and every
    # kind kept its row, its name now plain text.
    assert page.get_by_role("link", name="1 email queued in Testing").count() == 0
    assert kinds.get_by_role("link").count() == 0
    assert kinds.bounding_box()["y"] == top
    assert page.get_by_role("progressbar").count() == 0
    assert (
        page.locator(ANNOUNCER).text_content() == "No Family email send is in progress."
    )
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
    expect(
        page.get_by_text("No Family email send is in progress right now")
    ).to_be_visible(timeout=10000)
    expect(announcer).to_have_text("No Family email send is in progress.")
    # A fixed 5-second pace, not the usual 2-second start and back-off; it
    # keeps checking after the send ends.
    gaps = [later - earlier for earlier, later in pairwise(polls[:3])]
    assert len(polls) >= 3 and all(4.5 <= gap < 8 for gap in gaps), gaps


# Counts each poll timer the page arms (its 5-second fixed pace, and the
# 30-second pace past its limit), so a test can wait until the page has read
# a poll's answer and scheduled the next one before it moves the clock again.
COUNT_ARMED_POLLS = """() => {
    const setTimeout = window.setTimeout;
    window.armedPolls = 0;
    window.slowPolls = 0;
    window.setTimeout = function (callback, delay, ...rest) {
        if (delay === 5000) window.armedPolls += 1;
        if (delay === 30000) window.slowPolls += 1;
        return setTimeout.call(window, callback, delay, ...rest);
    };
}"""


@pytest.mark.parametrize("jump", ["between_polls", "during_a_poll"])
def test_watching_outlasts_the_old_hour_while_the_send_progresses(
    page, component_origin, jump
):
    """The limit counts from the last change, so a long watch keeps going.

    A page opened up to an hour before a send must follow it to the end.
    Each poll that brings new counts restarts the 3-hour limit; once the
    counts stop changing for that long, the page slows to one check every
    30 seconds rather than stopping (it has no Refresh link, #1586), and
    says nothing under the region.

    The clock is paused and only moves when the test moves it, and each
    jump waits until the page has re-armed its poll timer: the page arms the
    next poll only after the previous answer arrives, so jumping earlier
    would skip that timer (#425). The ``during_a_poll`` case deliberately
    jumps 150 minutes while the fourth poll is in flight, as a computer
    waking from sleep would: the page must still check once more at its pace before it
    slows, because that answer predates the jump.
    """
    from playwright.sync_api import expect

    from .conftest import NOW
    from .waits import eventually, recorded

    polls = []
    held = []

    def respond(route):
        """New counts on the first three polls, then the same ones."""
        polls.append(monotonic())
        if jump == "during_a_poll" and len(polls) == 4:
            held.append(route)  # Answered after the clock jump below.
            return
        step = min(len(polls), 3)
        body = status(NOW, sent=480 + 10 * step, remaining=600 - 10 * step)
        route.fulfill(content_type="text/html", body=body)

    def poll(minutes, count):
        """Jump ``minutes`` ahead; the poll it fires is answered and re-armed."""
        page.clock.fast_forward(minutes * 60 * 1000)
        recorded(page, polls, count)
        eventually(page, "window.armedPolls", count)

    page.clock.install(time=NOW)
    page.clock.pause_at(NOW + timedelta(seconds=1))
    page.route("**" + STATUS, respond)
    page.goto(component_origin + PAGE)
    page.evaluate(COUNT_ARMED_POLLS)
    problem = page.locator(".live-status-problem")
    for count in (1, 2, 3):
        # Two hours after load in all, well past the old one-hour limit.
        poll(40, count)
    expect(page.get_by_text("530 of 1,100 emails finished (48%)")).to_be_visible()
    assert problem.is_hidden()
    # Unchanged counts no longer renew the limit: 40 + 150 minutes later the
    # page slows down, after one last poll at its pace that still shows no
    # change.
    if jump == "between_polls":
        poll(40, 4)
        page.clock.fast_forward(150 * 60 * 1000)
    else:
        page.clock.fast_forward(40 * 60 * 1000)
        recorded(page, polls, 4)
        page.clock.fast_forward(150 * 60 * 1000)
        held[0].fulfill(
            content_type="text/html", body=status(NOW, sent=510, remaining=570)
        )
        eventually(page, "window.armedPolls", 4)
        assert problem.is_hidden()
        page.clock.fast_forward(5000)
    recorded(page, polls, 5)
    eventually(page, "window.slowPolls", 1)
    assert page.evaluate("window.armedPolls") == 4
    # Every 30 seconds from then on, with nothing said under the region.
    page.clock.fast_forward(30 * 1000)
    recorded(page, polls, 6)
    eventually(page, "window.slowPolls", 2)
    assert problem.is_hidden()
    assert page.evaluate("window.armedPolls") == 4
