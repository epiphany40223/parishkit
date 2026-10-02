"""Status pages mark a live region pending exactly while their work can change.

live-status-v1.js keeps polling only while the region carries
data-live-pending, so each page's running, finished and failed states are
checked here at the template boundary: running shows the worded indicator and
stays pending; success and failure stop polling and say so plainly.
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.accounts.campaign_family_test_views import _pending

REQUEST = UUID(int=164)
SCRIPT = (
    Path(__file__).parents[2]
    / "src/parishkit/stewardship/accounts/static/stewardship/live-status-v1.js"
)


def region(html, name):
    """Return the opening tag of the named live region."""
    match = re.search(rf'<[^>]*data-live-status="{name}"[^>]*>', html)
    assert match, f"no live region {name!r}"
    return match.group(0)


def pending(html, name):
    """True when the page asks the script to keep following its work."""
    return "data-live-pending" in region(html, name)


@pytest.mark.parametrize(
    ("state", "live", "text"),
    [
        ("staged", True, "Applying your change"),
        ("validating", True, "Applying your change"),
        ("applied", False, "Applied: your change is saved"),
        ("failed", False, "Not applied"),
        ("cancelled", False, "Not applied"),
    ],
)
def test_configuration_request_follows_until_applied_or_refused(state, live, text):
    """Running shows the indicator; both outcomes stop polling."""
    receipt = SimpleNamespace(state=state, request_id=REQUEST)
    html = render_to_string(
        "stewardship/configuration-request.html", {"receipt": receipt}
    )
    assert pending(html, "configuration") is live
    assert text in html
    assert ("live-running" in html) is live
    assert "live-status-v1.js" in html
    assert "status-refresh-v1.js" not in html


@pytest.mark.parametrize(
    ("state", "is_pending", "live", "text"),
    [
        ("installing", True, True, "Checking and installing the new key"),
        ("awaiting_ack", True, True, "waiting for the parts of the system that use it"),
        ("applied", False, False, "Installed and confirmed"),
        ("failed", False, False, "Not applied"),
    ],
)
def test_credential_status_follows_until_confirmed_or_refused(
    state, is_pending, live, text
):
    """The replacement page keeps following installation and consumer checks."""
    receipt = SimpleNamespace(state=state, request_id=REQUEST)
    html = render_to_string(
        "stewardship/credential-status.html",
        {"receipt": receipt, "pending": is_pending},
    )
    assert pending(html, "credential") is live
    assert text in html
    assert "Refresh status" in html


def task(state):
    """A background task row with the fields the status templates read."""
    return SimpleNamespace(
        id=REQUEST,
        name="Report export",
        type="report_export",
        state=state,
        active=True,
        attempt=1,
        retry_sequence=0,
        initiator_id=None,
        created_at="2026-09-28T02:00:00+00:00",
        updated_at=None,
        heartbeat_at=None,
        lease_expires_at=None,
        progress=SimpleNamespace(phase="working", total=0, current=0, display=None),
    )


@pytest.mark.parametrize(
    ("state", "live", "text"),
    [
        ("queued", True, "Still working"),
        ("running", True, "Still working"),
        ("retry_wait", True, "Waiting to try again"),
        ("succeeded", False, "Finished successfully"),
        ("failed", False, "This task failed"),
        ("cancelled", False, "This task was cancelled"),
    ],
)
def test_background_task_follows_until_terminal(state, live, text):
    """Task details stop polling at every terminal task state."""
    html = render_to_string("stewardship/background-task.html", {"task": task(state)})
    assert pending(html, "task") is live
    assert text in html
    # The status fragment the page polls carries the same region.
    fragment = render_to_string(
        "stewardship/background-task-status.html", {"task": task(state)}
    )
    assert pending(fragment, "task") is live
    assert "data-live-url" not in region(fragment, "task")


def test_background_task_polls_its_passive_fragment_not_the_page():
    """Polling the audited page would log a view every few seconds (#308)."""
    html = render_to_string(
        "stewardship/background-task.html",
        {"task": task("running"), "status_url": "/admin/background/task/x/status"},
    )
    assert 'data-live-url="/admin/background/task/x/status"' in region(html, "task")


@pytest.mark.parametrize(
    ("item", "unsettled"),
    [
        ({"message_state": None, "state": "queued"}, True),
        ({"message_state": None, "state": "prepared"}, False),
        ({"message_state": None, "state": "failed"}, False),
        ({"message_state": "pending", "state": "prepared"}, True),
        ({"message_state": "retry_wait", "state": "prepared"}, True),
        ({"message_state": "submitting", "state": "prepared"}, True),
        ({"message_state": "delivered", "state": "prepared"}, False),
        ({"message_state": "permanent_failure", "state": "prepared"}, False),
        ({"message_state": "delivery_unknown", "state": "prepared"}, False),
        ({"message_state": "cancelled", "state": "prepared"}, False),
    ],
)
def test_family_test_list_follows_only_unsettled_sends(item, unsettled):
    """A sent or failed test stops the list's polling; a queued one keeps it."""
    assert _pending(item) is unsettled


def test_script_backs_off_pauses_and_stops_at_terminal_states():
    """Guard the polling contract the pages rely on."""
    source = SCRIPT.read_text()
    assert "const DELAYS = [2000, 2000, 3000, 4000, 6000, 8000, 10000];" in source
    assert 'document.visibilityState !== "visible"' in source
    assert "Couldn't check status — retrying." in source
    # Only pending regions start polling, and a settled region finishes.
    assert 'region.hasAttribute("data-live-pending")' in source
    assert "else finish();" in source
    # Status reads never alter state: the script only issues GETs, swaps the
    # region in place (never reloads a POST response) and waits while the
    # Admin is using a control inside it.
    assert "method:" not in source
    assert "location.reload" not in source
    assert "if (busy()) {" in source
    # An unchanged poll must not rebuild the live region (no repeated
    # announcements), and the ticking elapsed time stays out of it.
    assert "fresh.innerHTML === lastMarkup" in source


def cleanup(state, *, cancelling=False):
    """Render the Testing cleanup page for one durable request state."""
    from parishkit.stewardship.campaigns.domain import Percentage

    status = SimpleNamespace(
        state=state, request_id=REQUEST, checkpoint_sequence=1, processed_count=1
    )
    live = state in {"cleanup_queued", "cleanup_running", "cleanup_retry_wait"} or (
        cancelling and state != "cancelled"
    )
    row = task("running" if live else "succeeded")
    row.updated_at = datetime(2026, 9, 28, 2, tzinfo=UTC)
    return render_to_string(
        "stewardship/go-live-cleanup.html",
        {
            "campaign": SimpleNamespace(
                pk=REQUEST, active_configuration=SimpleNamespace(name="Campaign")
            ),
            "status": status,
            "completion": Percentage(1, 2),
            "task": row,
            "cancelling": cancelling,
            "controls": {},
            "live_pending": live,
        },
    )


@pytest.mark.parametrize(
    ("state", "live", "text"),
    [
        ("cleanup_running", True, "Testing cleanup is running"),
        ("cleanup_complete", False, "Cleanup is complete"),
        ("cleanup_failed", False, "Cleanup failed"),
        ("cancelled", False, "Cleanup was cancelled"),
    ],
)
def test_testing_cleanup_follows_until_complete_or_failed(state, live, text):
    """Cleanup progress stops polling when complete, failed or cancelled."""
    html = cleanup(state)
    assert pending(html, "cleanup") is live
    assert text in html


def test_running_indicator_hides_the_ticking_elapsed_time_from_screen_readers():
    """Only the stable running message is announced, not a per-second clock."""
    html = render_to_string(
        "stewardship/live-running.html",
        {"message": "Working", "since": datetime(2026, 9, 28, 2, tzinfo=UTC)},
    )
    assert '<span class="live-since" aria-hidden="true">' in html
    assert "Working" in html


def test_elapsed_time_uses_the_server_clock_and_whole_units():
    """The ticker corrects for the computer's clock and matches waited_words."""
    source = SCRIPT.read_text()
    assert 'getAttribute("data-server-now")' in source
    assert "Date.now() + skew" in source
    # Whole units rounded down, with hours: never "2 minutes" at 90 seconds
    # or "600 minutes ago".
    assert 'seconds < 3600 ? [Math.floor(seconds / 60), "minute"]' in source
    assert '[Math.floor(seconds / 3600), "hour"]' in source


def test_script_supports_a_fixed_pace_and_change_only_announcements():
    """A region may ask for a fixed pace and change-only announcements (#413).

    Without data-live-interval the back-off above still applies, and a
    region without an announcer element announces nothing extra.
    """
    source = SCRIPT.read_text()
    assert 'region.getAttribute("data-live-interval")' in source
    assert "Math.min(Math.max(interval, 2000), 60000)" in source
    assert "fixed || DELAYS[" in source
    assert 'region.getAttribute("data-live-announce")' in source
    assert "if (!announcer || text === announced) return;" in source


def test_script_lets_a_region_renew_its_own_watching_limit():
    """A region may set its own watching limit, renewed by new content (#413).

    The limit is at most 3 hours and counts from the last poll that brought
    new content; other regions keep the one-hour limit from page load. It is
    judged from when the latest check was sent, so a clock jump while a check
    is in flight still leaves one fresh check before watching stops (#425).
    """
    source = SCRIPT.read_text()
    assert "const GIVE_UP_MS = 60 * 60 * 1000;" in source
    assert 'region.getAttribute("data-live-give-up")' in source
    assert "Math.min(ownLimit, 3 * GIVE_UP_MS) : GIVE_UP_MS" in source
    assert "if (renews) started = Date.now();" in source
    assert "if (asked - started > giveUp) {" in source
    assert '|| "Refresh status"' in source
    assert "Use ${refreshLabel} to check again." in source
