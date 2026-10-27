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


def credential(target, state, *, installed=timedelta(minutes=1)):
    """One credential request row, as ``finalization_status`` reads it,
    installed (``updated_at``) ``installed`` before the server clock of
    ``finishing_context``."""
    return {
        "target": target,
        "request_id": uuid4(),
        "request__state": state,
        "request__cleanup_reason": "",
        "request__updated_at": datetime.now(UTC) - installed,
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


@pytest.mark.parametrize("width", [320, 1280])
def test_finishing_setup_asks_for_the_operator_only_once_overdue(
    page, component_origin, axe_source, width
):
    """#2228: an installed credential's box is In progress, with the live
    count and no server operator, while the services pick it up; 10 minutes
    after its installation (the server's clock) it changes in place to
    Waiting for the server operator with what to ask, and returns to done
    once the acknowledgements arrive."""
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    page.clock.install(time=NOW)

    def waiting(installed, acknowledged=0):
        """The ParishSoft key installed ``installed`` ago, acknowledged by
        ``acknowledged`` of its two services."""
        return finishing_context(
            checkpoint="validating",
            prepared=False,
            credentials=[
                credential("parishsoft", "awaiting_ack", installed=installed)
                | {"acknowledged": acknowledged}
            ],
        )

    polls = answer(
        page,
        SETUP_FINISHING_STATUS,
        [
            finishing_status(waiting(timedelta(minutes=9, seconds=58))),
            finishing_status(waiting(timedelta(minutes=10))),
            finishing_status(waiting(timedelta(minutes=12), acknowledged=2)),
        ],
    )
    page.goto(component_origin + "/setup-installation")
    page.evaluate(MARK)
    step = page.locator('[data-finishing-step="parishsoft"]')
    shown = step.locator(".stage-body:not(.reserve-ghost)")
    page.clock.fast_forward(2000)
    recorded(page, polls, 1)
    has_text(shown.locator(".stage-status"), "In progress")
    visible(shown.get_by_text("0 of 2 services acknowledged."))
    assert step.locator(".spinner").count() == 1
    assert "server operator" not in shown.inner_text()
    # Still working, as before the poll: nothing new to announce.
    assert "server operator" not in page.locator("[data-live-announcer]").inner_text()
    box = step.bounding_box()
    page.clock.fast_forward(2000)
    recorded(page, polls, 2)
    has_text(shown.locator(".stage-status"), "Waiting for the server operator")
    visible(
        shown.get_by_text(
            "Taking longer than expected. Ask the server operator to check the "
            "worker and mail-dispatch services."
        )
    )
    visible(shown.get_by_text("0 of 2 services acknowledged."))
    assert step.locator(".spinner").count() == 0
    has_text(page.locator("[data-live-announcer]"), "Waiting for the server operator.")
    # The box changed in place: the overdue form was reserved from the start.
    assert step.bounding_box() == box
    assert axe_violations(page, axe_source) == []
    page.clock.fast_forward(2000)
    recorded(page, polls, 3)
    has_text(shown.locator(".stage-status"), "Done")
    assert page.evaluate(MARKED) == "kept"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
