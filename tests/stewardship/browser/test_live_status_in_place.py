"""Live-status pages update themselves, and Dismiss acts in place (#519 PR
7a, #1586).

Each page is served at its real address (live_status_components), where its
own reads and Dismiss form lead; none has a Refresh link. A route answers a
read with a fresh version of the page, so the region visibly changes. Each
test sets a mark outside every region, which only a full page load could
lose.
"""

import re
import time

import pytest

from .live_status_components import (
    CHANGE_RUNNING,
    DISMISS,
    FAMILY_TESTS_PENDING,
    INTEGRATION,
    KEEP,
    KEEP_EMPTY,
    PROGRESS,
    PROGRESS_STATUS,
    TASK_PAGE,
    TASK_STATUS,
)
from .test_in_place import MARK, MARKED, VIEWPORT, count_requests
from .waits import has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def expect(locator):
    """Load the optional browser assertion library only after explicit opt-in."""
    from playwright.sync_api import expect as browser_expect

    return browser_expect(locator)


FOCUSED = "selector => document.activeElement === document.querySelector(selector)"


def answer_reads(page, origin, address, *fixtures):
    """Answer each in-place read of ``address`` (a fetch, never the page's
    own load) with the next of ``fixtures``, the last one from then on.
    Returns the list of reads answered."""
    reads = []

    def answer(route):
        """Serve the next fixture to a fetch; let the page load through."""
        if route.request.resource_type == "document":
            route.continue_()
            return
        reads.append(route.request.url)
        fixture = fixtures[min(len(reads), len(fixtures)) - 1]
        route.fulfill(
            response=route.fetch(url=origin + fixture, method="GET"),
        )

    page.route(lambda url: url.split("#")[0] == origin + address, answer)
    return reads


# A link or button that re-reads the page: its text starts "Refresh", other
# than the actions that start a ParishSoft download (the Admin menu's
# Refresh from ParishSoft, Refresh now). None is drawn on a converted page
# (#1586).
PAGE_REFRESH = re.compile(r"^\s*Refresh(?! from ParishSoft| now)")


def refresh_controls(page):
    """The page's links and buttons that would re-read the page itself."""
    return page.locator("a, button").filter(has_text=PAGE_REFRESH)


def test_mail_queue_updates_itself(page, component_origin):
    """The Mail queue has no Refresh link (#1586) and brings its
    passive status's fresh counts in by itself, at its fixed pace: no
    reload, focus left where it was."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + PROGRESS)
    page.evaluate(MARK)
    assert refresh_controls(page).count() == 0
    expect(page.locator("main").get_by_text("900").first).to_be_visible(timeout=15000)
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("document.activeElement === document.body")


def test_recent_family_tests_update_themselves(page, component_origin):
    """While a Family test is on its way, the recent tests re-read the page
    by themselves until it settles; the page has no Refresh link (#1586)."""
    page.set_viewport_size(VIEWPORT)
    reads = answer_reads(
        page, component_origin, FAMILY_TESTS_PENDING, "/live-family-tests-later"
    )
    page.goto(component_origin + FAMILY_TESTS_PENDING)
    page.evaluate(MARK)
    assert refresh_controls(page).count() == 0
    hidden(page.get_by_text("Sending Family tests…"))
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("document.activeElement === document.body")
    assert len(reads) == 1


def test_pending_status_updates_itself_then_stops(page, component_origin):
    """A configuration change being applied is followed by itself to the
    end: the first read finds it still applying, the next that it is
    applied, all without a reload; watching then stops, with one status
    line."""
    page.set_viewport_size(VIEWPORT)
    reads = answer_reads(
        page,
        component_origin,
        CHANGE_RUNNING,
        "/live-running-pending",
        "/live-running-applied",
    )
    page.goto(component_origin + CHANGE_RUNNING)
    page.evaluate(MARK)
    assert refresh_controls(page).count() == 0
    visible(page.get_by_role("heading", name="Applied: your change is saved"))
    assert page.evaluate(MARKED) == "kept"
    assert len(reads) == 2
    # Watching stopped once the status finished: no further reads.
    page.wait_for_timeout(3000)
    assert len(reads) == 2
    assert page.locator(".live-status-problem").count() == 1


def test_reload_while_pending_shows_the_same_page_and_keeps_updating(
    page, component_origin
):
    """A browser reload while a configuration change is still pending
    lands on the same address and status (the server draws it again), and
    the page goes on updating by itself until it is applied (#1586,
    #1568)."""
    page.set_viewport_size(VIEWPORT)
    # Unrouted, every read of the page finds the change still pending.
    page.goto(component_origin + CHANGE_RUNNING)
    visible(page.get_by_text("Applying your change", exact=False))
    page.reload()
    assert page.url == component_origin + CHANGE_RUNNING
    visible(page.get_by_text("Applying your change", exact=False))
    page.evaluate(MARK)
    reads = answer_reads(
        page, component_origin, CHANGE_RUNNING, "/live-running-applied"
    )
    visible(page.get_by_role("heading", name="Applied: your change is saved"))
    assert page.evaluate(MARKED) == "kept"
    assert len(reads) == 1


def test_integration_dismiss_is_in_place(page, component_origin):
    """Dismiss on a finished key change posts once, follows the redirect back
    to the settings page and swaps the status line in place: no reload, the
    result gone, the dismissal announced, and the address the redirect's."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + INTEGRATION)
    page.evaluate(MARK)
    posts = count_requests(page, "POST", DISMISS)
    page.get_by_role("button", name="Dismiss").dblclick()
    has_text(
        page.get_by_role("status").filter(has_text="Result dismissed."),
        "Result dismissed.",
    )
    hidden(page.get_by_text("ParishSoft did not accept the new API key."))
    assert page.evaluate(MARKED) == "kept"
    assert (
        page.url == component_origin + INTEGRATION + "?dismissed=1#integration-status"
    )
    assert posts == [component_origin + DISMISS]
    assert page.evaluate(FOCUSED, "main h1")


def test_progress_keeps_one_poller(page, component_origin):
    """The Mail queue is watched for as long as a send runs, at the
    page's own pace, by one watcher: no two polls come closer together than
    half that pace."""
    page.set_viewport_size(VIEWPORT)
    polls = []
    page.on(
        "request",
        lambda request: (
            polls.append(time.monotonic() * 1000)
            if request.url.endswith(PROGRESS_STATUS)
            else None
        ),
    )
    page.goto(component_origin + PROGRESS)
    polls.clear()
    page.wait_for_timeout(16000)
    assert len(polls) >= 2, polls
    gaps = [later - earlier for earlier, later in zip(polls, polls[1:], strict=False)]
    assert min(gaps) > 2500, gaps
    assert page.locator(".live-status-problem").count() == 1


def test_finished_task_shows_its_final_steps_in_place(page, component_origin):
    """When a watched refresh ends, its Task history table shows the final
    steps without a reload (#869): the status poll brings the finished bar,
    and the page is then re-read once, in place, for the table. Focus and
    the address stay as they were, and no further reads follow."""
    page.set_viewport_size(VIEWPORT)
    polls = answer_reads(
        page, component_origin, TASK_STATUS, "/live-task-finished-status"
    )
    reads = answer_reads(page, component_origin, TASK_PAGE, "/live-task-finished")
    page.goto(component_origin + TASK_PAGE)
    page.evaluate(MARK)
    history = page.locator("[data-table-region]")
    visible(history.get_by_text("Downloading from ParishSoft"))
    assert history.get_by_role("cell", name="Finished", exact=True).count() == 0
    # The poll finds the run finished; the follow-up re-reads the page.
    visible(page.get_by_text("Finished successfully."))
    visible(history.get_by_role("cell", name="Finished", exact=True))
    visible(history.get_by_text("Making the new data current").first)
    # The bar and the table agree: both at the last of 4,000 records.
    visible(page.get_by_text("4,000 of 4,000 records checked"))
    assert page.evaluate(MARKED) == "kept"
    assert page.url == component_origin + TASK_PAGE
    assert page.evaluate("document.activeElement === document.body")
    page.wait_for_timeout(3000)
    assert len(polls) == 1, polls
    assert len(reads) == 1, reads


def test_a_kept_element_survives_swaps_and_marks_a_changed_part(page, component_origin):
    """A kept element (data-live-keep, #530) is moved, not rebuilt, by a
    swap: its tick and its focus stay and do not hold the swap. A part about
    something the status no longer names is marked changed on its reserved
    line, its button held. An empty kept element takes the fresh contents."""
    page.goto(component_origin + KEEP)
    box = page.get_by_label("Tick")
    box.check()
    box.focus()
    page.evaluate("document.querySelector('[data-live-keep]').dataset.marked = 'yes'")
    expect(page.locator("[data-step]")).to_have_text("two", timeout=15000)
    kept = page.locator('[data-live-keep="box"]')
    assert kept.get_attribute("data-marked") == "yes"
    expect(box).to_be_checked()
    expect(box).to_be_focused()
    assert kept.get_attribute("data-live-keep-key") == "b"
    expect(page.locator("[data-live-stale-note]")).to_have_text("Changed.")
    expect(page.get_by_role("button", name="Go")).to_be_disabled()
    assert page.locator("[data-live-keep-for][data-live-stale]").count() == 1

    page.goto(component_origin + KEEP_EMPTY)
    expect(page.locator("[data-live-keep] [data-filled]")).to_have_text(
        "Filled", timeout=15000
    )
