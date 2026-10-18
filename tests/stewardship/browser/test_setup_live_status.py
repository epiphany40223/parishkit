"""Load parish data and Finishing setup update in place (#1354).

Each page watches its passive status fragment (live-status-v1.js) and swaps
in the fresh region while the work runs, then stops. Each test marks the
page (a mark only a full load could lose), keeps focus on a control outside
the region, and checks that the page above the region did not move.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

from .conftest import (
    NOW,
    SETUP_FINISHING_STATUS,
    SETUP_LOAD_STATUS,
    finishing_context,
    load_collections,
)
from .setup_live_components import finishing_status, load_status
from .test_in_place import MARK, MARKED
from .test_setup_mail import place
from .waits import focused, has_text, recorded, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)


def answer(page, address, bodies):
    """Answer each poll of ``address`` with the next of ``bodies``, the last
    one from then on; return the polls."""
    polls = []

    def respond(route):
        """Record the poll and answer it with the next status fragment."""
        polls.append(route.request)
        body = bodies[min(len(polls), len(bodies)) - 1]
        route.fulfill(content_type="text/html", body=body)

    page.route(lambda url: url.endswith(address), respond)
    return polls


def observation(**values):
    """One source-load progress reading, as ``source_progress`` returns it."""
    return {
        "server_now": NOW.isoformat(),
        "task_id": str(uuid4()),
        "task_state": "running",
        "setup_state": "loading",
        "phase": "fetching",
        "current": 0,
        "total": 0,
        "active": True,
        "collections": load_collections(5, finished=57, expected=213),
        "started_at": (NOW - timedelta(seconds=75)).isoformat(),
        "heartbeat_at": (NOW - timedelta(seconds=3)).isoformat(),
        "idle_at": (NOW + timedelta(minutes=30)).isoformat(),
        "watchdog_at": (NOW + timedelta(hours=2)).isoformat(),
        "absolute_at": (NOW + timedelta(hours=12)).isoformat(),
    } | values


@pytest.mark.parametrize("width", [320, 1280])
def test_load_parish_data_moves_on_in_place(page, component_origin, axe_source, width):
    """The load's phases, collections and counts move on without a reload; an
    open Technical details stays open; completion shows Continue and stops."""
    from playwright.sync_api import expect

    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    page.clock.install(time=NOW)
    renewals = []

    def renew(route):
        """The visible page's idle renewal, which draws nothing."""
        renewals.append(route.request)
        route.fulfill(
            json=observation(task_id=urlsplit(route.request.url).path.split("/")[-1])
        )

    page.route("**/admin/setup/source/*?format=json", renew)
    saving = observation(phase="staging", current=1234, total=5000)
    saving["collections"] = load_collections(7)
    done = observation(
        phase="validating",
        task_state="succeeded",
        setup_state="collecting",
        active=False,
        current=9000,
        total=9000,
        collections=load_collections(7),
    )
    polls = answer(
        page,
        SETUP_LOAD_STATUS,
        [load_status(saving, None), load_status(done, None)],
    )
    page.goto(component_origin + "/setup-source-progress")
    page.evaluate(MARK)
    assert "downloading" in page.locator("[data-progress-summary]").inner_text()
    expect(page.locator("[data-progress-elapsed]")).to_contain_text("minute ago")
    details = page.locator("details", has_text="Technical details")
    summary = details.locator("summary")
    summary.click()
    summary.focus()
    above = place(page, "h1")
    assert axe_violations(page, axe_source) == []
    page.clock.fast_forward(2000)
    has_text(page.locator("[data-task-counts]"), "1,234 of 5,000 (25%)")
    visible(page.locator("[data-load-records]"))
    assert "In progress" in page.locator('[data-load-phase="staging"]').inner_text()
    # The disclosure stayed open and shows the new phase; focus stayed on it.
    assert details.evaluate("node => node.open")
    has_text(details.locator("[data-task-phase]"), "staging")
    focused(summary)
    assert page.evaluate(MARKED) == "kept"
    assert same_place(place(page, "h1"), above)
    page.clock.fast_forward(10000)
    visible(page.get_by_role("link", name="Continue to the next step"))
    has_text(page.locator("[data-live-announcer]"), str(done_summary()))
    assert page.evaluate(MARKED) == "kept"
    focused(summary)
    seen = len(polls)
    page.clock.fast_forward(60000)
    page.wait_for_timeout(200)
    assert len(polls) == seen
    assert all(poll.method == "GET" for poll in polls)
    assert all(request.method == "POST" for request in renewals)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


def done_summary():
    """The summary a finished load announces."""
    from parishkit.stewardship.accounts.setup_progress_views import SUMMARIES

    return SUMMARIES["done"]


def credential(target, state):
    """One credential request row, as ``finalization_status`` reads it."""
    return {
        "target": target,
        "request_id": uuid4(),
        "request__state": state,
        "request__cleanup_reason": "",
        "consumers": 2,
        "acknowledged": 0,
    }


@pytest.mark.parametrize("width", [320, 1280])
def test_finishing_setup_moves_on_in_place(page, component_origin, axe_source, width):
    """Each step moves on without a reload, the step-up notice appears in
    place when it is needed, and completion shows Continue, drops Cancel
    setup and stops."""
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    page.clock.install(time=NOW)
    now = datetime.now(UTC)
    testing = finishing_context(
        checkpoint="validating",
        prepared=False,
        credentials=[credential("parishsoft", "testing")],
        # A sign-in older than five minutes, before Google Workspace's
        # credential started: the step-up notice is needed.
        authenticated_at=now - timedelta(minutes=10),
    )
    completed = {"overall": "completed"}
    polls = answer(
        page,
        SETUP_FINISHING_STATUS,
        [finishing_status(testing), finishing_status(completed)],
    )
    page.goto(component_origin + "/setup-installation")
    page.evaluate(MARK)
    assert page.locator("[data-step-up-notice]").count() == 0
    details = page.locator("details", has_text="Technical details")
    summary = details.locator("summary")
    summary.focus()
    above = place(page, "h1")
    page.clock.fast_forward(2000)
    recorded(page, polls, 1)
    step = page.locator('[data-finishing-step="parishsoft"]')
    has_text(step.locator(".stage-status"), "In progress")
    visible(step.get_by_text("Checking the credential with the provider."))
    visible(page.locator("[data-step-up-notice]"))
    focused(summary)
    assert page.evaluate(MARKED) == "kept"
    assert same_place(place(page, "h1"), above)
    assert axe_violations(page, axe_source) == []
    page.clock.fast_forward(2000)
    recorded(page, polls, 2)
    visible(page.get_by_role("heading", name="Setup is complete"))
    visible(page.locator(".setup-actions").get_by_role("link", name="Continue"))
    assert page.get_by_role("button", name="Cancel initial setup").count() == 0
    assert page.locator("[data-step-up-notice]").count() == 0
    has_text(page.locator("[data-live-announcer]"), "Setup is complete.")
    focused(summary)
    assert page.evaluate(MARKED) == "kept"
    page.clock.fast_forward(60000)
    page.wait_for_timeout(200)
    assert len(polls) == 2
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert axe_violations(page, axe_source) == []


def test_finishing_setup_failure_stops_watching(page, component_origin):
    """A failed step is shown in place with what to do, and watching stops."""
    page.clock.install(time=NOW)
    failed = finishing_context(
        checkpoint="validating",
        prepared=False,
        credentials=[credential("parishsoft", "failed")],
    )
    polls = answer(page, SETUP_FINISHING_STATUS, [finishing_status(failed)])
    page.goto(component_origin + "/setup-installation")
    page.evaluate(MARK)
    page.clock.fast_forward(2000)
    recorded(page, polls, 1)
    visible(page.locator(".notice-error").get_by_text("Setup could not finish."))
    has_text(page.locator("[data-live-announcer]"), "Setup could not finish.")
    # Cancel setup stays: it is what the failure asks for.
    visible(page.get_by_role("button", name="Cancel initial setup"))
    page.clock.fast_forward(60000)
    page.wait_for_timeout(200)
    assert len(polls) == 1
    assert page.evaluate(MARKED) == "kept"
