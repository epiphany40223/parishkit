"""The Campaign calendar's operational events, without a database (#1808).

The refresh times come from the saved schedule, resolved on each day around
a send in the campaign's zone; a send's notes say when a refresh falls in
its preparation window or it was sent during a backup.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from parishkit.stewardship.accounts.schedule_calendar_events import (
    NOTE_BACKUP,
    NOTE_REFRESH,
    CalendarEvent,
    refresh_events,
    refresh_times,
    send_notes,
    skip_windows,
)
from parishkit.stewardship.accounts.schedule_table import ScheduleRow

ZONE = "America/New_York"


def schedule(*times, frequency="daily"):
    """A ``refresh_status.refresh_schedule`` answer with ``times`` daily."""
    return {
        "timezone": ZONE,
        "nightly_time": times[0],
        "frequency": frequency,
        "full_refresh_times": times,
        "rules": False,
    }


def send(due, kind="reminder"):
    """One calendar send at ``due``."""
    return ScheduleRow(
        id="s", label="Reminder 1", kind=kind, subject="", status="upcoming", due=due
    )


def test_refresh_times_are_the_daily_list_or_none():
    """A daily schedule's times, sorted; none without ParishSoft or when frequent."""
    assert refresh_times(schedule("02:00", "14:00")) == ("02:00", "14:00")
    assert refresh_times(None) == ()
    assert refresh_times(schedule("02:00", frequency="hourly")) == ()


def test_refreshes_are_resolved_on_the_days_around_each_send():
    """Each time on the send's parish-local day and the day either side."""
    # 2054-10-12 12:00 in New York (EDT, UTC-4).
    events = refresh_events(
        schedule("02:00"), [send(datetime(2054, 10, 12, 16, tzinfo=UTC))]
    )
    assert [event.start for event in events] == [
        datetime(2054, 10, day, 6, tzinfo=UTC) for day in (11, 12, 13)
    ]
    assert all(event.kind == "refresh" and event.daily for event in events)
    assert refresh_events(None, [send(datetime(2054, 10, 12, 16, tzinfo=UTC))]) == []


def test_a_refresh_follows_daylight_saving_as_the_scheduler_does():
    """02:00 local is 07:00 UTC once standard time starts (2054-11-01)."""
    events = refresh_events(
        schedule("02:00"), [send(datetime(2054, 11, 2, 17, tzinfo=UTC))]
    )
    assert datetime(2054, 11, 2, 7, tzinfo=UTC) in [event.start for event in events]


def test_a_send_notes_a_refresh_in_its_preparation_window():
    """A refresh in the two hours before a Family send still to come is noted."""
    due = datetime(2054, 10, 12, 13, tzinfo=UTC)  # 9:00 AM EDT
    now = due - timedelta(days=1)
    assert send_notes(send(due), schedule("02:00", "08:00"), [], now) == [
        str(NOTE_REFRESH)
    ]
    # 02:00 is long before; a report email or a passed send has no note.
    assert send_notes(send(due), schedule("02:00"), [], now) == []
    assert send_notes(send(due, "report"), schedule("08:00"), [], now) == []
    assert send_notes(send(due), schedule("08:00"), [], due) == []


def test_a_send_notes_a_backup_it_was_sent_during():
    """A send's time inside a finished backup's start and end is noted."""
    due = datetime(2054, 10, 12, 6, 5, tzinfo=UTC)
    backup = CalendarEvent("backup", due - timedelta(minutes=5), due + timedelta(1))
    after = CalendarEvent("backup", due + timedelta(minutes=1), due + timedelta(2))
    assert send_notes(send(due), None, [backup], due) == [str(NOTE_BACKUP)]
    assert send_notes(send(due), None, [after], due) == []


def test_milestones_are_not_daily():
    """A go-live milestone always shows, whatever the day holds."""
    assert not CalendarEvent("milestone", datetime(2054, 10, 1, tzinfo=UTC)).daily


def test_an_initial_invitation_has_no_refresh_note():
    """Only a reminder is prepared ahead, so an Initial invitation gets no note."""
    due = datetime(2054, 10, 12, 13, tzinfo=UTC)
    now = due - timedelta(days=1)
    wanted = schedule("02:00", "08:00")
    assert send_notes(send(due, "initial"), wanted, [], now) == []
    assert send_notes(send(due, "reminder"), wanted, [], now) == [str(NOTE_REFRESH)]


def test_a_note_is_for_production_only():
    """Mail is prepared ahead only in Production; Testing gets no refresh note."""
    due = datetime(2054, 10, 12, 13, tzinfo=UTC)
    now = due - timedelta(days=1)
    wanted = schedule("02:00", "08:00")
    assert send_notes(send(due), wanted, [], now, production=False) == []


def test_skipping_around_emails_keeps_only_the_nightly_time_in_the_note():
    """The scheduler skips a non-nightly time in a lead window, never the nightly."""
    due = datetime(2054, 10, 12, 13, tzinfo=UTC)  # 9:00 AM EDT
    now = due - timedelta(days=1)
    skipping = schedule("02:00", "08:00") | {"skips_around_emails": True}
    assert send_notes(send(due), skipping, [], now) == []
    # The nightly time itself in the window is still noted.
    nightly = schedule("08:00", "14:00") | {"skips_around_emails": True}
    assert send_notes(send(due), nightly, [], now) == [str(NOTE_REFRESH)]


def test_skipped_refreshes_inside_a_send_window_are_not_drawn():
    """A non-nightly time in a reminder's lead window or send is left out."""
    zone = ZoneInfo(ZONE)
    reminder = send(datetime(2054, 10, 12, 13, tzinfo=UTC))  # 9:00 AM EDT
    initial = send(datetime(2054, 10, 13, 13, 30, tzinfo=UTC), "initial")  # 9:30
    now = reminder.due - timedelta(days=2)
    sends = [reminder, initial]
    # 08:00 is in the reminder's lead window and 09:30 in its sending hour.
    # The initial invitation's window starts at its due time, so 09:00 on the
    # 13th is outside it but 10:00 is inside.
    wanted = schedule("02:00", "08:00", "09:00", "09:30", "10:00") | {
        "skips_around_emails": True
    }
    drawn = refresh_events(wanted, sends, skip_windows(sends, now))
    local = {
        (
            event.start.astimezone(zone).day,
            event.start.astimezone(zone).strftime("%H:%M"),
        )
        for event in drawn
    }
    assert {(12, "08:00"), (12, "09:30"), (13, "10:00"), (13, "09:30")}.isdisjoint(
        local
    )
    assert {(12, "02:00"), (12, "10:00"), (13, "09:00")} <= local
    # Without windows (skipping off, or Testing) every time is drawn.
    assert len(refresh_events(wanted, sends)) > len(drawn)
    # A past send and a report email have no window.
    assert skip_windows([send(reminder.due, "report")], now) == []
    assert skip_windows([reminder], reminder.due) == []
