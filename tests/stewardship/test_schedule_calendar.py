"""The Campaign calendar's sends: every send of the saved schedules (#469)."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from parishkit.stewardship.accounts import digest_skips
from parishkit.stewardship.accounts.schedule_calendar_views import (
    WEEK,
    calendar_sends,
)

from .test_schedule_table import BEFORE, OWNER, VALUES, saved

ZONE = ZoneInfo(VALUES["timezone"])
CAMPAIGN_DAYS = [date(2054, 10, 1) + timedelta(days=n) for n in range(31)]


def local(send):
    """A send's campaign-local date and time."""
    return send.due.astimezone(ZONE)


def test_one_time_sends_keep_their_rows_and_digests_send_on_each_day():
    """Sending order, with each digest expanded the way the scheduler runs it."""
    sends = calendar_sends(saved(), VALUES, {}, BEFORE)
    assert [send.due for send in sends] == sorted(send.due for send in sends)
    once = [send for send in sends if send.kind in {"initial", "reminder"}]
    assert [(send.label, local(send).date()) for send in once] == [
        ("Initial invitation", date(2054, 10, 1)),
        ("Reminder 1", date(2054, 10, 10)),
        ("Reminder 2", date(2054, 10, 20)),
    ]
    # A daily digest reports each campaign day the next morning, so its last
    # send is the day after the campaign closes.
    daily = [send for send in sends if send.kind == "daily_digest"]
    assert [local(send).date() for send in daily] == [
        day + timedelta(days=1) for day in CAMPAIGN_DAYS
    ]
    assert {local(send).time().isoformat() for send in daily} == {"06:00:00"}
    weekly = [send for send in sends if send.kind == "weekly_digest"]
    assert [local(send).date() for send in weekly] == [
        day for day in CAMPAIGN_DAYS if day.weekday() == 0
    ]
    # Each digest send is its digest's row: the same name, status and Edit,
    # whose address names that send's slot (#1658).
    assert len({(send.label, send.status) for send in daily}) == 1
    assert daily[0].status == "repeats"
    assert {send.edit_url for send in daily} == {
        f"/admin/campaign/schedules/{send.id}/?send={send.slot}" for send in daily
    }
    assert daily[0].slot == "2054-10-01"


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


def test_a_digests_past_sends_are_muted_and_its_later_ones_linked():
    """A digest day whose time has passed is "Past", read-only and unlinked."""
    later = datetime(2054, 10, 5, 12, tzinfo=UTC)
    daily = [
        send
        for send in calendar_sends(saved(), VALUES, {}, later)
        if send.kind == "daily_digest"
    ]
    past = [send for send in daily if send.due <= later]
    # October 2 through 5, each at 6:00 AM in New York.
    assert [local(send).day for send in past] == [2, 3, 4, 5]
    assert {(send.status, send.status_label, send.read_only) for send in past} == {
        ("passed", "Past", True)
    }
    assert {send.edit_url for send in past} == {None}
    upcoming = daily[len(past) :]
    assert {send.status for send in upcoming} == {"repeats"}
    assert all(send.edit_url for send in upcoming)


def test_every_digest_send_is_listed_however_many_pages_it_takes(monkeypatch):
    """The scheduler's evaluation is read page by page to its last send."""
    monkeypatch.setattr(digest_skips, "PAGE_DATES", 7)
    daily = [
        send
        for send in calendar_sends(saved(), VALUES, {}, BEFORE)
        if send.kind == "daily_digest"
    ]
    assert [local(send).date() for send in daily] == [
        day + timedelta(days=1) for day in CAMPAIGN_DAYS
    ]


def test_sends_keep_their_wall_clock_time_across_a_daylight_saving_change():
    """New York falls back on Sunday, November 1, 2054, inside the campaign.

    Each daily digest still sends at 6:00 AM local time, one per day with none
    missing or repeated, so its UTC time moves an hour; a weekly digest's
    Monday sends do the same.
    """
    values = VALUES | {"end_date": "2054-11-10"}
    sends = calendar_sends(saved(), values, {}, BEFORE)
    daily = [send for send in sends if send.kind == "daily_digest"]
    assert [local(send).date() for send in daily] == [
        date(2054, 10, 2) + timedelta(days=n) for n in range(41)
    ]
    assert {local(send).time().isoformat() for send in daily} == {"06:00:00"}
    hours = {local(send).date(): send.due.hour for send in daily}
    assert hours[date(2054, 10, 31)] == 10
    assert hours[date(2054, 11, 1)] == 11
    weekly = [send for send in sends if send.kind == "weekly_digest"]
    assert [local(send).date() for send in weekly][-2:] == [
        date(2054, 11, 2),
        date(2054, 11, 9),
    ]
    assert {local(send).hour for send in weekly} == {9}
    assert {send.due.hour for send in weekly[-2:]} == {14}


def test_no_schedules_means_no_sends():
    """An empty campaign has an empty calendar, not an error."""
    assert calendar_sends([], VALUES, {}, BEFORE) == []


def test_sends_at_one_moment_keep_the_tables_sending_order():
    """The Initial invitation stays first at a tie, as the list orders it."""
    owner = OWNER["id"]
    rows = saved() + [
        # A weekly digest on Thursday, October 1, at the invitation's time.
        {
            "id": "ffffffff-ffff-4fff-8fff-ffffffffffff",
            "values": saved()[0]["values"] | {"weekday": 3, "campaign_id": owner},
        }
    ]
    rows[-1]["values"]["time"] = "09:00:00"
    sends = calendar_sends(rows, VALUES, {}, BEFORE)
    assert [send.label for send in sends[:2]] == [
        "Initial invitation",
        "Weekly Admin digest",
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


def test_a_skipped_digest_send_is_listed_muted_as_skipped():
    """A digest's skipped send (#1658) stays on the calendar, unlinked."""
    rows = saved()
    daily = next(row for row in rows if row["values"]["kind"] == "daily_digest")
    daily["values"] = daily["values"] | {"skip_dates": ["2054-10-03"]}
    sends = [
        send
        for send in calendar_sends(rows, VALUES, {}, BEFORE)
        if send.kind == "daily_digest"
    ]
    assert len(sends) == len(CAMPAIGN_DAYS)
    (skipped,) = [send for send in sends if send.slot == "2054-10-03"]
    assert (skipped.status, skipped.status_label, skipped.read_only) == (
        "skipped",
        "Skipped",
        True,
    )
    assert skipped.edit_url is None
    assert {send.status for send in sends if send is not skipped} == {"repeats"}
