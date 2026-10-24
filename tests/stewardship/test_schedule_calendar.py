"""The Campaign calendar's sends: every send of the saved schedules (#469)."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from parishkit.stewardship.accounts.schedule_calendar_views import (
    WEEK,
    calendar_sends,
)
from parishkit.stewardship.accounts.schedule_table import ScheduleRow

from .test_schedule_table import BEFORE, OWNER, VALUES, saved

ZONE = ZoneInfo(VALUES["timezone"])


def local(send):
    """A send's campaign-local date and time."""
    return send.due.astimezone(ZONE)


def test_each_family_schedule_is_one_send_in_time_order():
    """The Initial invitation and each reminder, by send time."""
    sends = calendar_sends(saved(), VALUES, {}, BEFORE)
    assert [(send.label, local(send).date()) for send in sends] == [
        ("Initial invitation", date(2054, 10, 1)),
        ("Reminder 1", date(2054, 10, 10)),
        ("Reminder 2", date(2054, 10, 20)),
    ]
    assert {send.status for send in sends} == {"upcoming"}
    assert all(send.edit_url and send.slot is None for send in sends)


def test_a_schedule_that_has_run_is_listed_without_its_edit_page():
    """Read-only as on the list: its status says it sent and nothing links it."""
    rows = saved()
    initial = next(row for row in rows if row["values"]["kind"] == "initial")
    later = datetime(2054, 10, 5, tzinfo=UTC)
    sends = calendar_sends(rows, VALUES, {initial["id"]: {"delivered": 3}}, later)
    first = sends[0]
    assert (first.label, first.status, first.read_only) == (
        "Initial invitation",
        "sent",
        True,
    )
    assert first.edit_url is None
    reminder = next(send for send in sends if send.label == "Reminder 1")
    assert reminder.status == "upcoming"
    assert reminder.edit_url


def test_no_schedules_means_no_sends():
    """An empty campaign has an empty calendar, not an error."""
    assert calendar_sends([], VALUES, {}, BEFORE) == []


def test_sends_at_one_moment_keep_the_tables_sending_order():
    """The Initial invitation stays first at a tie, as the list orders it."""
    owner = OWNER["id"]
    rows = saved() + [
        # A reminder on Thursday, October 1, at the invitation's time.
        {
            "id": "00000000-0000-4000-8000-000000000000",
            "values": saved()[0]["values"]
            | {"date": "2054-10-01", "campaign_id": owner},
        }
    ]
    sends = calendar_sends(rows, VALUES, {}, BEFORE)
    assert [send.label for send in sends[:2]] == [
        "Initial invitation",
        "Reminder 1",
    ]
    assert sends[0].due == sends[1].due


def test_the_month_grid_week_starts_on_sunday():
    """Short headings shown, full names read; Sunday through Saturday."""
    assert [str(long) for _short, long in WEEK] == [
        "Sunday",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
    ]


def test_only_upcoming_family_sends_are_movable():
    """Move… (#1521): the Initial invitation and reminders until they run.

    A send that has run, or whose time passed without sending (missed),
    does not move.
    """
    sends = calendar_sends(saved(), VALUES, {}, BEFORE)
    assert all(send.movable and not send.digest for send in sends)
    rows = saved()
    initial = next(row for row in rows if row["values"]["kind"] == "initial")
    later = datetime(2054, 10, 5, tzinfo=UTC)
    after = calendar_sends(rows, VALUES, {initial["id"]: {"delivered": 3}}, later)
    assert (after[0].status, after[0].movable) == ("sent", False)
    missed = calendar_sends(rows, VALUES, {}, later)[0]
    assert (missed.status, missed.edit_url is not None, missed.movable) == (
        "missed",
        True,
        False,
    )


def test_every_family_email_holds_its_own_moment_but_no_report_does():
    """The calendar's exposure of Edit's same-moment rule (PR #2095, R50).

    Edit's review refuses two Family emails at one date and time, so each
    initial invitation and reminder, sent or not, is a moment a moved send
    can't take; a report email's send is not Family mail and never clashes.
    """
    rows = saved()
    initial = next(row for row in rows if row["values"]["kind"] == "initial")
    later = datetime(2054, 10, 5, tzinfo=UTC)
    sends = calendar_sends(rows, VALUES, {initial["id"]: {"delivered": 3}}, later)
    assert [(send.status, send.own_moment) for send in sends] == [
        ("sent", True),
        ("upcoming", True),
        ("upcoming", True),
    ]
    report = ScheduleRow(
        id="r",
        label="Daily report",
        kind="report",
        subject="",
        status="upcoming",
        due=later,
    )
    assert report.own_moment is False
