"""Planning series edits: this and following, the whole series, delete (#469).

Pure tests of ``accounts.series_edits.plan`` over applied records. The
commands against a real database are in
database/test_campaign_series_postgresql.py.
"""

import pytest

from parishkit.stewardship.accounts.series_edits import (
    SeriesEditError,
    plan,
    rule_text,
)
from parishkit.stewardship.campaigns.recurrence import canonical_rule

SERIES = "00000000-0000-4000-8000-0000000000a1"
EMAIL = "00000000-0000-4000-8000-000000000004"
OTHER = "00000000-0000-4000-8000-000000000005"
# Mondays in October 2054: the 5th, 12th, 19th and 26th.
RULE = canonical_rule(
    {"frequency": "weekly", "start": "2054-10-01", "end": "2054-10-31", "weekdays": [0]}
)
DAYS = ("2054-10-05", "2054-10-12", "2054-10-19", "2054-10-26")
IDS = [f"00000000-0000-4000-8000-00000000010{index}" for index in range(4)]
SCHEDULES = {
    identifier: {"date": day, "time": "09:00:00", "template_version": EMAIL}
    for identifier, day in zip(IDS, DAYS, strict=True)
}
RECORD = {"id": SERIES, "values": {"rule": RULE, "schedules": sorted(IDS)}}


def edit(read_only=(), **options):
    """Plan an edit of the four-Monday series."""
    return plan(RECORD, SCHEDULES, set(read_only), **options)


def test_a_new_time_from_a_reminder_cuts_the_series_there():
    """Earlier reminders stay under a rule ending the day before; later ones move."""
    result = edit(scope="following", start=IDS[2], time_of_day="10:00")
    assert result["remove"] == IDS[2:]
    assert result["add"] == [
        {"date": day, "time": "10:00", "template_version": EMAIL} for day in DAYS[2:]
    ]
    assert result["series"] == {
        "id": None,
        "rule": RULE | {"start": "2054-10-19"},
        "keep": [],
    }
    assert result["changes"] == [
        {
            "operation": "update",
            "section": "schedule_series",
            "id": SERIES,
            "values": {"rule": RULE | {"end": "2054-10-18"}, "schedules": IDS[:2]},
        }
    ]


def test_an_unchanged_reminder_keeps_its_identity():
    """A new rule's date whose time and email match keeps its member as it is."""
    rule = RULE | {"weekdays": [0, 3]}
    result = edit(scope="series", rule=rule)
    assert result["remove"] == []
    # Each Thursday is new, with the first reminder's time and email.
    assert [values["date"] for values in result["add"]] == [
        "2054-10-01",
        "2054-10-08",
        "2054-10-15",
        "2054-10-22",
        "2054-10-29",
    ]
    assert {values["time"] for values in result["add"]} == {"09:00:00"}
    # Nothing stays behind, so the series itself takes the new rule.
    assert result["series"] == {"id": SERIES, "rule": rule, "keep": sorted(IDS)}
    assert result["changes"] == []


def test_the_same_time_written_differently_is_unchanged():
    """``09:00`` and ``09:00:00`` are one time, so nothing changes."""
    result = edit(scope="series", time_of_day="09:00")
    assert result == {"remove": [], "add": [], "series": None, "changes": []}


def test_a_rule_change_with_the_same_reminders_still_saves_the_rule():
    """An end the rule does not land on gives the same dates, but is remembered."""
    rule = RULE | {"end": "2054-10-27"}
    result = edit(scope="series", rule=rule)
    assert result["remove"] == [] and result["add"] == []
    assert result["series"] == {"id": SERIES, "rule": rule, "keep": sorted(IDS)}
    assert result["changes"] == []


def test_the_whole_series_starts_at_its_first_unsent_reminder():
    """Sent reminders stay in the series; the rest take the new email."""
    result = edit(read_only=IDS[:1], scope="series", template_version=OTHER)
    assert result["remove"] == IDS[1:]
    assert {values["template_version"] for values in result["add"]} == {OTHER}
    assert result["changes"][0]["values"]["schedules"] == IDS[:1]


def test_a_dropped_date_is_removed():
    """A new rule that leaves out a date removes that date's reminder."""
    rule = RULE | {"end": "2054-10-20"}
    result = edit(scope="series", rule=rule)
    assert result["remove"] == [IDS[3]] and result["add"] == []
    assert result["series"]["keep"] == sorted(IDS[:3])


def test_delete_removes_only_unsent_reminders_and_the_series():
    """A sent reminder stays as an ordinary reminder."""
    result = edit(read_only=IDS[:1], scope="delete")
    assert result == {
        "remove": IDS[1:],
        "add": [],
        "series": None,
        "changes": [
            {"operation": "remove", "section": "schedule_series", "id": SERIES}
        ],
    }


def test_a_sent_reminder_earlier_that_day_points_to_this_and_following():
    """The whole series cannot start mid-day after a sent member: say what to do."""
    schedules = SCHEDULES | {
        IDS[1]: SCHEDULES[IDS[1]] | {"date": DAYS[0], "time": "10:00:00"}
    }
    with pytest.raises(SeriesEditError, match='"following"'):
        plan(RECORD, schedules, {IDS[0]}, scope="series")


@pytest.mark.parametrize(
    ("options", "read_only", "message"),
    [
        ({"scope": "following", "start": EMAIL}, (), "not in this series"),
        ({"scope": "series"}, IDS, "already sent"),
        ({"scope": "following", "start": IDS[1]}, IDS[3:], "from this date on"),
        (
            {"scope": "following", "start": IDS[2], "rule": RULE},
            (),
            "on or after 2054-10-19",
        ),
    ],
)
def test_an_edit_that_would_touch_sent_mail_is_refused(options, read_only, message):
    """Unknown reminder, all sent, a sent one after the cut, a rule before it."""
    with pytest.raises(SeriesEditError, match=message):
        edit(read_only=read_only, **options)


def test_per_date_times_come_before_the_one_time():
    """The page's times by date (#558) win; a date without one takes the time."""
    result = edit(
        scope="following",
        start=IDS[2],
        time_of_day="10:00:00",
        times={DAYS[2]: "11:00:00"},
    )
    assert [row["time"] for row in result["add"]] == ["11:00:00", "10:00:00"]


@pytest.mark.parametrize(
    ("rule", "text"),
    [
        (RULE, "Every Monday, October 1, 2054 – October 31, 2054"),
        (
            RULE | {"weekdays": [0, 3, 4]},
            "Every Monday, Thursday and Friday, October 1, 2054 – October 31, 2054",
        ),
        (
            {"frequency": "daily", "start": "2054-10-01", "end": "2054-10-02"},
            "Every day, October 1, 2054 – October 2, 2054",
        ),
        (
            {
                "frequency": "monthly",
                "start": "2054-10-01",
                "end": "2054-12-31",
                "monthly": {"day": 15},
            },
            "Day 15 of every month, October 1, 2054 – December 31, 2054",
        ),
        (
            {
                "frequency": "monthly",
                "start": "2054-10-01",
                "end": "2054-12-31",
                "monthly": {"ordinal": -1, "weekday": 4},
            },
            "The last Friday of every month, October 1, 2054 – December 31, 2054",
        ),
    ],
)
def test_rules_read_in_plain_words(rule, text):
    """Every frequency is a sentence with its calendar dates, never raw."""
    assert rule_text(rule) == text
