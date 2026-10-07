"""The scheduled emails table: order, labels, status and which editors start open."""

from datetime import UTC, datetime, timedelta

from parishkit.stewardship.accounts.schedule_forms import Schedules
from parishkit.stewardship.accounts.schedule_table import (
    attach,
    schedule_rows,
    status,
)

from .campaign_factory import campaign, schedule
from .test_schedule_forms import data_for

OWNER = campaign()
VALUES = OWNER["values"]
BEFORE = datetime(2054, 9, 1, tzinfo=UTC)
AFTER = datetime(2054, 11, 15, tzinfo=UTC)


def saved():
    """An initial invitation, two reminders and both digests, out of order."""
    owner = OWNER["id"]
    return [
        schedule(owner, kind="weekly_digest", date=None, weekday=0),
        schedule(owner, kind="reminder", date="2054-10-20"),
        schedule(owner, kind="daily_digest", date=None, time="06:00:00"),
        schedule(owner, kind="reminder", date="2054-10-10"),
        schedule(owner),
    ]


def formset(rows, data=None):
    """The page's formset over ``rows``, bound to ``data`` when given."""
    return Schedules(
        data,
        prefix="schedules",
        previous=rows,
        templates=[],
        campaign_id=OWNER["id"],
        campaign=VALUES,
    )


def test_status_follows_due_time_and_work_counts():
    """Upcoming before the time; then sending, sent or passed without a send."""
    due = datetime(2054, 10, 1, 13, tzinfo=UTC)
    assert status("initial", due, {}, BEFORE) == "upcoming"
    assert status("initial", due, {"cancellable": 3}, BEFORE) == "upcoming"
    assert status("reminder", due, {"blocking": 1}, AFTER) == "sending"
    assert status("reminder", due, {"cancellable": 2}, AFTER) == "sending"
    assert status("reminder", due, {"delivered": 5}, AFTER) == "sent"
    assert status("reminder", due, {"outboxes": 5}, AFTER) == "sent"
    # Failures alone read as a failed send; with any delivery it still sent.
    assert status("reminder", due, {"failed": 1}, AFTER) == "failed"
    assert status("reminder", due, {"failed": 1, "delivered": 4}, AFTER) == "sent"
    # A run that settled every Family without an email (coalesced or empty
    # dispositions, counted in ``covered``) is done, not "time passed".
    assert status("reminder", due, {"covered": 12}, AFTER) == "done"
    # Work that blocks a change, prepared ahead of the send time, is shown
    # and locked as Preparing rather than Upcoming.
    assert status("reminder", due, {"blocking": 1}, BEFORE) == "preparing"
    assert status("initial", due, {}, AFTER) == "missed"
    assert status("daily_digest", None, {"delivered": 9}, AFTER) == "repeats"


def test_rows_follow_sending_order_and_number_reminders():
    """The table lists the formset's order; reminders count up as they send."""
    rows = schedule_rows(formset(saved()).previous, VALUES, {}, BEFORE)
    assert [row.label for row in rows] == [
        "Initial invitation",
        "Reminder 1",
        "Reminder 2",
        "Daily Admin digest",
        "Weekly Admin digest",
    ]
    assert [row.status for row in rows] == ["upcoming"] * 3 + ["repeats"] * 2
    assert rows[0].due < rows[1].due < rows[2].due
    # Digests repeat: no one-time send, but a next send time and a cadence.
    daily, weekly = rows[3:]
    assert daily.due is None and daily.next_due is not None and daily.weekday is None
    assert weekly.weekday == "Monday" and weekly.next_due.weekday() == 0
    assert not any(row.read_only for row in rows)


def test_a_sent_schedule_is_read_only_with_its_last_send():
    """A past Family send is read-only and summarizes what it delivered."""
    rows = formset(saved()).previous
    summary = {
        rows[0]["id"]: {"delivered": 1031, "failed": 2},
        rows[1]["id"]: {"blocking": 1},
    }
    table = schedule_rows(rows, VALUES, summary, AFTER)
    first, reminder, later = table[:3]
    assert (first.status, first.status_label) == ("sent", "Sent")
    assert first.read_only and first.last_send
    assert (first.delivered, first.failed) == (1031, 2)
    assert reminder.status == "sending" and reminder.read_only
    assert not reminder.last_send
    # After the campaign, a reminder that never sent stays editable.
    assert later.status == "missed" and not later.read_only
    # Digests never lock, and have no Family send to link to.
    assert table[3].next_due is None and not table[3].read_only
    assert not table[3].last_send


def test_editors_start_closed_unless_changed_or_in_error():
    """After a refused preview, a changed or invalid editor stays in view."""
    rows = formset(saved()).previous
    assert not any(row.open for row in attach(formset(rows), VALUES, {}, BEFORE))
    data = data_for(rows)
    data["schedules-1-date"] = "2054-10-12"
    data["schedules-2-date"] = "2055-01-01"
    data["schedules-3-DELETE"] = "on"
    bound = formset(rows, data)
    bound.is_valid()
    opened = attach(bound, VALUES, {}, BEFORE)
    # Unchanged rows stay closed even though Django's has_changed compares
    # the saved strings with typed values and would call them all changed.
    assert [row.open for row in opened] == [False, True, True, True, False]
    # Each saved form carries its row; the blank new row carries none.
    assert [form.schedule_row for form in bound.forms[:5]] == opened
    assert not hasattr(bound.forms[5], "schedule_row")


def test_a_past_send_editor_opens_only_to_show_an_error():
    """An unchanged past send stays closed; an error on it is still shown."""
    rows = formset(saved()).previous
    summary = {rows[0]["id"]: {"delivered": 3}}
    data = data_for(rows)
    bound = formset(rows, data)
    bound.is_valid()
    assert not attach(bound, VALUES, summary, AFTER)[0].open
    data["schedules-0-time"] = "not a time"
    bound = formset(rows, data)
    bound.is_valid()
    assert attach(bound, VALUES, summary, AFTER)[0].open


def test_a_past_send_editor_opens_to_show_a_posted_change():
    """A change posted into a schedule that has since run stays in view.

    The preview is refused (the schedule is read-only now), and a hidden
    editor would post that change again unseen on every later Preview.
    """
    rows = formset(saved()).previous
    summary = {rows[0]["id"]: {"delivered": 3}}
    data = data_for(rows)
    data["schedules-0-date"] = "2054-10-02"
    bound = formset(rows, data)
    bound.is_valid()
    first = attach(bound, VALUES, summary, AFTER)[0]
    assert first.read_only and first.open


def test_a_run_with_nobody_to_send_to_is_read_only():
    """A reminder that ran but sent nothing cannot be moved and re-planned."""
    rows = formset(saved()).previous
    reminder = rows[1]["id"]
    table = schedule_rows(rows, VALUES, {reminder: {"covered": 40}}, AFTER)
    assert table[1].status == "done" and table[1].read_only
    assert table[1].status_label == "Done"
    assert not table[1].last_send
    failed = schedule_rows(rows, VALUES, {reminder: {"failed": 3}}, AFTER)[1]
    assert failed.status_label == "Sent (failed)"
    assert failed.read_only and failed.last_send


def test_work_prepared_ahead_is_read_only_and_waiting_is_not():
    """Preparing locks the row; "Not sent yet" stays editable."""
    rows = formset(saved()).previous
    summary = {rows[1]["id"]: {"blocking": 2}}
    table = schedule_rows(rows, VALUES, summary, BEFORE)
    assert (table[1].status_label, table[1].read_only) == ("Preparing", True)
    assert table[2].status == "upcoming" and not table[2].read_only
    waiting = schedule_rows(rows, VALUES, {}, AFTER)[0]
    assert (waiting.status_label, waiting.read_only) == ("Not sent yet", False)


def test_rows_tolerate_a_send_time_far_past_the_campaign():
    """A digest whose campaign is long over has no next send."""
    later = AFTER + timedelta(days=400)
    table = schedule_rows(formset(saved()).previous, VALUES, {}, later)
    assert table[3].next_due is None and table[4].next_due is None
