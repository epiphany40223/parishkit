"""Finishing setup updates in place (#1354).

The page watches its passive status fragment (live-status-v1.js) and swaps
in the fresh region while the work runs, then stops. Each test marks the
page (a mark only a full load could lose), keeps focus on a control outside
the region, and checks that the page above the region did not move.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from .conftest import (
    NOW,
    SETUP_FINISHING_STATUS,
    finishing_context,
)
from .setup_live_components import finishing_status
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
    """Each step moves on without a reload, and completion shows Continue,
    drops Cancel setup and stops."""
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    page.clock.install(time=NOW)
    now = datetime.now(UTC)
    testing = finishing_context(
        checkpoint="validating",
        prepared=False,
        credentials=[credential("parishsoft", "testing")],
        # A sign-in older than five minutes, before Google Workspace's
        # credential started: it still installs, with no notice (#1547).
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
    # A control outside the region (the About panel's) keeps focus.
    summary = page.locator("details.about-page > summary")
    summary.focus()
    above = place(page, "h1")
    page.clock.fast_forward(2000)
    recorded(page, polls, 1)
    step = page.locator('[data-finishing-step="parishsoft"]')
    # The box's shown words, not its invisible reserved forms (#1903).
    shown = step.locator(".stage-body:not(.reserve-ghost)")
    has_text(shown.locator(".stage-status"), "In progress")
    visible(shown.get_by_text("Checking with the provider."))
    assert "sign-in" not in page.locator("main").inner_text()
    focused(summary)
    assert page.evaluate(MARKED) == "kept"
    assert same_place(place(page, "h1"), above)
    assert axe_violations(page, axe_source) == []
    page.clock.fast_forward(2000)
    recorded(page, polls, 2)
    visible(page.get_by_role("heading", name="Setup is complete"))
    visible(page.locator(".setup-actions").get_by_role("link", name="Continue"))
    # The notice carries no backup advice (#2152): just the result.
    done = page.locator("[data-finishing-done]")
    assert done.locator("p").all_inner_texts() == [
        "The system is configured. Continue to the administration home."
    ]
    assert page.get_by_role("button", name="Cancel initial setup").count() == 0
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
