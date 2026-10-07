"""Live-status pages refresh in place, and Dismiss acts in place (#519 PR 7a).

Each page is served at its real address (live_status_components), where its
Refresh link and Dismiss form lead. A route answers a Refresh with a fresh
version of the page, so the region visibly changes. Each test sets a mark
outside every region, which only a full page load could lose.
"""

import time

import pytest

from .live_status_components import (
    CREDENTIAL,
    CREDENTIAL_RUNNING,
    DISMISS,
    FAMILY_TESTS,
    INTEGRATION,
    PROGRESS,
    PROGRESS_STATUS,
)
from .test_in_place import MARK, MARKED, VIEWPORT, count_requests
from .waits import has_text, hidden, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

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


@pytest.mark.parametrize(
    "address, fresh, link, message, shown",
    [
        (
            PROGRESS,
            "/live-progress-later",
            "Refresh progress",
            "Progress refreshed.",
            "900",
        ),
        (
            FAMILY_TESTS,
            "/live-family-tests-later",
            "Refresh status",
            "Status refreshed.",
            "Family DUID",
        ),
    ],
)
def test_refresh_links_reread_in_place(
    page, component_origin, address, fresh, link, message, shown
):
    """Refresh re-reads the page once and swaps the status in place: no
    reload, focus kept on the link, the refresh announced, and the fresh
    counts or tests shown."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + address)
    page.evaluate(MARK)
    reads = answer_reads(page, component_origin, address, fresh)
    refresh = page.get_by_role("link", name=link, exact=True)
    refresh.click()
    has_text(page.get_by_role("status").filter(has_text=message), message)
    visible(page.locator("main").get_by_text(shown).first)
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("document.activeElement.textContent") == link
    assert len(reads) == 1


def test_refreshed_pending_status_is_watched_again(page, component_origin):
    """A Refresh that brings back a status still being worked on is watched
    by live-status-v1.js again, so it finishes by itself: the first read
    answers that the key is installed and waiting, the next poll that it is
    confirmed, all without a reload."""
    page.set_viewport_size(VIEWPORT)
    page.goto(component_origin + CREDENTIAL)
    page.evaluate(MARK)
    visible(page.get_by_role("heading", name="Not applied"))
    reads = answer_reads(
        page,
        component_origin,
        CREDENTIAL,
        "/live-credential-pending",
        "/live-credential-applied",
    )
    page.get_by_role("link", name="Refresh status").click()
    has_text(
        page.get_by_role("status").filter(has_text="Status refreshed."),
        "Status refreshed.",
    )
    visible(page.get_by_text("Installed; waiting", exact=False))
    visible(page.get_by_role("heading", name="Installed and confirmed"))
    assert page.evaluate(MARKED) == "kept"
    assert len(reads) == 2
    # Watching stopped once the fresh status finished: no further reads.
    page.wait_for_timeout(3000)
    assert len(reads) == 2
    # One watcher, and so one status line, for the fresh region.
    assert page.locator(".live-status-problem").count() == 1


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


def test_refresh_replaces_a_running_watcher(page, component_origin):
    """A Refresh while the page is already watching a pending key change
    leaves exactly one watcher: the old one stops with its region, so the
    page reads its status once for the Refresh and once for the fresh
    watcher's poll that finds it confirmed, then stops, with one status
    line."""
    page.set_viewport_size(VIEWPORT)
    reads = answer_reads(
        page,
        component_origin,
        CREDENTIAL_RUNNING,
        "/live-running-pending",
        "/live-running-applied",
    )
    page.goto(component_origin + CREDENTIAL_RUNNING)
    page.evaluate(MARK)
    page.get_by_role("link", name="Refresh status").click()
    visible(page.get_by_role("heading", name="Installed and confirmed"))
    assert page.evaluate(MARKED) == "kept"
    # The Refresh's read, then the fresh watcher's one poll; the old
    # watcher would have polled the page too had it kept running.
    page.wait_for_timeout(4000)
    assert len(reads) == 2, reads
    assert page.locator(".live-status-problem").count() == 1


def test_refresh_keeps_one_progress_poller(page, component_origin):
    """Family email progress is watched for as long as a send runs. After a
    Refresh, its status is still polled at the page's own pace by one
    watcher: no two polls come closer together than half that pace."""
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
    answer_reads(page, component_origin, PROGRESS, "/live-progress-later")
    page.get_by_role("link", name="Refresh progress").click()
    has_text(
        page.get_by_role("status").filter(has_text="Progress refreshed."),
        "Progress refreshed.",
    )
    polls.clear()
    page.wait_for_timeout(16000)
    assert len(polls) >= 2, polls
    gaps = [later - earlier for earlier, later in zip(polls, polls[1:], strict=False)]
    assert min(gaps) > 2500, gaps
    assert page.locator(".live-status-problem").count() == 1
