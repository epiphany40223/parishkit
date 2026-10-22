"""Dates and mail schedules' suggested starting schedules (#1404 PR 6a).

``schedule_suggestions`` works out the set from the campaign's values and
saved emails alone: the Initial invitation on the start date at 9:00 AM and
one Reminder seven days before the end date at 9:00 AM, each sending the
first saved email of its mail type, with the rules that leave one out. No
report is suggested (#1786).
"""

from datetime import UTC, datetime

from parishkit.stewardship.accounts import schedule_suggestions

from .campaign_factory import campaign
from .content_factory import content

OWNER = "00000000-0000-4000-8000-000000000001"
NOW = datetime(2054, 9, 1, tzinfo=UTC)


def emails(*kinds):
    """One saved email per mail type in ``kinds``, in that order."""
    return [content(OWNER, kind="email", slot=kind) for kind in kinds]


def values(**overrides):
    """The factory campaign's values (October 2054, in the parish zone)."""
    return campaign(**overrides)["values"]


def test_the_full_set_sends_each_types_first_email():
    """Initial and Reminder, at the decided times; no report is suggested."""
    records = [
        content(OWNER, slot="welcome"),
        *emails("initial", "reminder", "weekly_digest", "initial"),
    ]
    assert schedule_suggestions.entries(values(), records, NOW) == [
        {
            "kind": "initial",
            "date": "2054-10-01",
            "time": "09:00",
            "template_version": records[1]["id"],
        },
        {
            "kind": "reminder",
            "date": "2054-10-24",
            "time": "09:00",
            "template_version": records[2]["id"],
        },
    ]


def test_a_type_without_an_email_is_left_out():
    """No reminder email: only what can be sent is suggested."""
    entries = schedule_suggestions.entries(values(), emails("initial"), NOW)
    assert [entry["kind"] for entry in entries] == ["initial"]
    # Without an Initial invitation email nothing is suggested at all.
    assert schedule_suggestions.entries(values(), emails("reminder"), NOW) == []


def test_a_short_campaign_has_no_reminder():
    """The Reminder must fall on a day after the start date."""
    records = emails("initial", "reminder")
    # Nine days: the Reminder (Oct 2) follows the Initial invitation.
    nine = values(start_date="2054-10-01", end_date="2054-10-09")
    assert [
        entry["kind"] for entry in schedule_suggestions.entries(nine, records, NOW)
    ] == [
        "initial",
        "reminder",
    ]
    # Eight days: the Reminder would fall on the start date itself.
    eight = values(start_date="2054-10-01", end_date="2054-10-08")
    kinds = [
        entry["kind"] for entry in schedule_suggestions.entries(eight, records, NOW)
    ]
    assert kinds == ["initial"]


def test_rows_are_drawn_as_saved_schedules_marked_not_saved_yet():
    """Each row resolves its send in the campaign's zone, status suggested."""
    records = emails("initial", "reminder", "weekly_digest")
    names = {row["id"]: f"name of {row['values']['slot']}" for row in records}
    rows = schedule_suggestions.rows(values(), records, OWNER, NOW, names)
    assert [row.label for row in rows] == [
        "Initial invitation",
        "Reminder 1",
    ]
    assert {row.status_label for row in rows} == {"Not saved yet"}
    assert not any(row.read_only for row in rows)
    assert [row.email for row in rows] == [
        "name of initial",
        "name of reminder",
    ]
    # 9:00 AM in the parish zone (Eastern daylight time on these dates).
    assert rows[0].due == datetime(2054, 10, 1, 13, tzinfo=UTC)
    assert rows[1].due == datetime(2054, 10, 24, 13, tzinfo=UTC)


def test_nothing_is_suggested_once_the_initial_send_time_has_passed():
    """A past Initial time would send at once, so no schedule is suggested."""
    records = emails("initial", "reminder", "weekly_digest")
    # Start date 2054-10-01, 9:00 AM Eastern daylight time = 13:00 UTC.
    just_before = datetime(2054, 10, 1, 12, 59, tzinfo=UTC)
    at = datetime(2054, 10, 1, 13, tzinfo=UTC)
    kinds = schedule_suggestions.entries(values(), records, just_before)
    assert [entry["kind"] for entry in kinds] == [
        "initial",
        "reminder",
    ]
    assert schedule_suggestions.entries(values(), records, at) == []
    assert schedule_suggestions.rows(values(), records, OWNER, at, {}) == []
