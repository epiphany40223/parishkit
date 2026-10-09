"""Windows around Family emails in which scheduled refreshes are skipped (#632)."""

from datetime import UTC, datetime, timedelta

from parishkit.stewardship.campaigns import family_schedule_planning
from parishkit.stewardship.source import send_hold, send_windows
from parishkit.stewardship.source.send_hold import SEND_ALLOWANCE
from parishkit.stewardship.source.send_windows import (
    Email,
    Window,
    email_windows,
    estimate,
    window_cause,
)

DUE = datetime(2026, 10, 6, 10, tzinfo=UTC)
HOUR = timedelta(hours=1)
ESTIMATES = {"initial": HOUR, "reminder": timedelta(minutes=75)}


def test_the_lead_window_is_the_bulk_sends_prepare_ahead():
    """The reminder's preparing window is the bulk send's lead window."""
    assert send_windows.PREPARE_AHEAD == family_schedule_planning.PREPARE_AHEAD


def test_a_near_retry_is_the_ordinary_retry_schedules_cap():
    """Ordinary retries count as send work; throttle and limit waits do not (#868)."""
    from parishkit.stewardship.jobs import family_mail_dispatch as dispatch
    from parishkit.stewardship.jobs import family_mail_tasks

    soon = send_hold.RETRY_SOON.total_seconds()
    assert dispatch.retry_delay(100) == soon
    assert soon >= family_mail_tasks.RECOVERY_RETRY_CAP_SECONDS
    assert min(dispatch.RECIPIENT_RETRY_SECONDS) > soon
    assert min(dispatch.LIMIT_RETRY_SECONDS.values()) > soon


def test_the_estimate_is_rounded_up_and_bounded():
    """Rounded up to 15 minutes, at least an hour, at most the allowance."""
    assert estimate(None) == HOUR
    assert estimate(timedelta(minutes=10)) == HOUR
    assert estimate(timedelta(minutes=61)) == timedelta(minutes=75)
    assert estimate(timedelta(minutes=75)) == timedelta(minutes=75)
    assert estimate(timedelta(hours=5)) == SEND_ALLOWANCE


def test_a_reminder_is_skipped_around_while_prepared_and_sent():
    """From two hours before its due time to the estimated end of its send."""
    preparing, sending = email_windows(
        Email("reminder", DUE), now=DUE - HOUR, estimates=ESTIMATES
    )
    assert preparing == Window(DUE - 2 * HOUR, DUE, "reminder_preparing")
    assert sending == Window(DUE, DUE + timedelta(minutes=75), "reminder_sending")


def test_an_initial_invitation_is_not_prepared_ahead():
    """Its window starts at its due time."""
    (window,) = email_windows(Email("initial", DUE), now=DUE, estimates=ESTIMATES)
    assert window == Window(DUE, DUE + HOUR, "initial_sending")


def test_a_send_with_active_work_stays_open_up_to_the_allowance():
    """While sending after its due time the window runs to the cap, no further."""
    later = DUE + timedelta(minutes=90)
    (window,) = email_windows(
        Email("initial", DUE, sending=True), now=later, estimates=ESTIMATES
    )
    assert window.end == DUE + SEND_ALLOWANCE
    # Before its due time active work does not stretch the window.
    (early,) = email_windows(
        Email("initial", DUE, sending=True), now=DUE - HOUR, estimates=ESTIMATES
    )
    assert early.end == DUE + HOUR


def test_windows_include_their_start_and_exclude_their_end():
    """A slot at the start is inside; a slot at the end is not."""
    windows = email_windows(Email("reminder", DUE), now=DUE, estimates=ESTIMATES)
    assert window_cause(windows, DUE - 2 * HOUR) == "reminder_preparing"
    assert window_cause(windows, DUE) == "reminder_sending"
    assert window_cause(windows, DUE + timedelta(minutes=75)) is None
    assert window_cause(windows, DUE - 2 * HOUR - timedelta(seconds=1)) is None
    assert window_cause([], DUE) is None


def test_overlapping_windows_name_the_earliest():
    """An initial send overlapping a later reminder's preparation names the send."""
    windows = [
        *email_windows(Email("initial", DUE), now=DUE, estimates=ESTIMATES),
        *email_windows(Email("reminder", DUE + 2 * HOUR), now=DUE, estimates=ESTIMATES),
    ]
    assert window_cause(windows, DUE + timedelta(minutes=30)) == "initial_sending"
    assert window_cause(windows, DUE + timedelta(minutes=90)) == "reminder_preparing"


def test_a_send_length_ignores_late_finishes_and_never_goes_negative():
    """Late retries past the allowance do not stretch the measured length."""
    from parishkit.stewardship.source.send_windows import send_length

    assert send_length(DUE, DUE + timedelta(minutes=50)) == timedelta(minutes=50)
    assert send_length(DUE, DUE - HOUR) == timedelta()
    # Every message finished after the allowance: the send took at least it.
    assert send_length(DUE, None) == SEND_ALLOWANCE
