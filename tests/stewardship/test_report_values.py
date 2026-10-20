"""A report email's stored values (report-emails spec, "Stored form", #1786)."""

from copy import deepcopy
from datetime import date

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.report_values import (
    name_key,
    repeat_bounds,
    report_values,
    validate_reports,
)

CAMPAIGN = "6f1c5a52-6c3e-4f2f-9a59-0f4b3c1f2e10"
USER = "0b8f2a8e-1b9c-4d1e-8f55-3b2f4c7d9e01"


def daily():
    """The converted Daily's values (spec, "Conversion")."""
    return {
        "campaign_id": CAMPAIGN,
        "kind": "report",
        "name": "Daily report",
        "subject": "{{ campaign_name }}",
        "intro_html": "",
        "intro_text": "",
        "recipients": [{"group": "administrators"}],
        "schedule": {
            "type": "repeat",
            "frequency": "daily",
            "from": "day_after_campaign_start",
            "until": "day_after_campaign_end",
            "time": "07:00:00",
        },
        "as_of": "previous_day_end",
        "blocks": [
            {"block": "campaign_totals", "version": 1, "options": {}},
            {"block": "response_funnel", "version": 1, "options": {}},
            {"block": "participation_charts", "version": 1, "options": {}},
            {"block": "day_by_day", "version": 1, "options": {"days": 5}},
            {"block": "compared_with_past_campaigns", "version": 1, "options": {}},
        ],
        "content_required": False,
        "skip_dates": ["2026-10-08"],
        "paused_ranges": [],
        "slot_basis": "report_date",
    }


def weekly():
    """The converted Weekly's values, with every recipient kind."""
    values = daily()
    values.update(
        name="Weekly report",
        intro_html="<p>Here are the additional information requests that are "
        "still open.</p>",
        intro_text="Here are the additional information requests that are still open.",
        recipients=[
            {"group": "administrators"},
            {"group": "staff"},
            {"user": USER},
            {"address": "council@example.org"},
        ],
        schedule={
            "type": "repeat",
            "frequency": "weekly",
            "weekdays": [0],
            "from": "campaign_start",
            "until": "first_send_after_campaign_end",
            "time": "08:00:00",
        },
        as_of="send_time",
        blocks=[
            {"block": "additional_information", "version": 1, "options": {}},
            {"block": "response_funnel", "version": 1, "options": {}},
        ],
        content_required=True,
        paused_ranges=[
            {
                "start": "2026-10-20T13:00:00.250000+00:00",
                "end": "2026-10-21T13:00:00+00:00",
            },
            {"start": "2026-10-22T13:00:00+00:00", "end": None},
        ],
        slot_basis="send_date",
    )
    return values


def test_the_converted_reports_are_valid():
    """Both converted reports and a one-time report validate unchanged."""
    once = daily()
    once["schedule"] = {"type": "once", "date": "2026-10-20", "time": "09:00:00"}
    for values in (daily(), weekly(), once):
        assert report_values(values) is values


def change(values, path, value):
    """``values`` with the member at ``path`` replaced (or removed when None)."""
    result = deepcopy(values)
    target = result
    for key in path[:-1]:
        target = target[key]
    if value is None:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return result


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("kind",), "daily_digest"),
        (("as_of",), None),
        (("name",), ""),
        (("name",), "x" * 81),
        (("name",), " Daily"),
        (("subject",), "x" * 255),
        (("subject",), "{{ family_name }}"),
        (("intro_text",), "{{ family_code }}"),
        (("intro_html",), None),
        (("recipients",), []),
        (("recipients",), [{"group": "ministry_leaders"}]),
        (("recipients",), [{"user": "not-a-uuid"}]),
        (("recipients",), [{"address": "Council@example.org"}]),
        (("recipients",), [{"address": "not an address"}]),
        (("recipients",), [{"group": "staff"}, {"group": "staff"}]),
        (("recipients",), [{"group": "staff", "user": USER}]),
        (("recipients",), [{"address": f"a{n}@example.org"} for n in range(26)]),
        (("schedule", "type"), "monthly"),
        (("schedule", "frequency"), "monthly"),
        (("schedule", "weekdays"), [0]),
        (("schedule", "from"), "campaign_end"),
        (("schedule", "until"), "2026-02-30"),
        (("schedule", "from"), "0001-01-01"),
        (("schedule", "time"), "7:00"),
        (("schedule", "time"), "07:00:00+00:00"),
        (("as_of",), "now"),
        (("slot_basis",), "due_date"),
        (("blocks",), []),
        (("blocks", 0, "block"), "nonexistent"),
        (("blocks", 0, "version"), 2),
        (("blocks", 0, "options"), {"days": 5}),
        (("blocks", 3, "options"), {"days": 15}),
        (("blocks", 3, "options"), {"days": True}),
        (("blocks", 1, "block"), "campaign_totals"),
        (("blocks", 4, "block"), "day_by_day"),
        (("content_required",), 1),
        (("skip_dates",), ["2026-10-09", "2026-10-08"]),
        (("skip_dates",), None),
        (("paused_ranges",), [{"start": "2026-10-20T13:00:00Z", "end": None}]),
    ],
)
def test_a_value_outside_the_stored_form_is_refused(path, value):
    """Each malformed value, missing key or broken rule is a configuration error."""
    with pytest.raises(ConfigError):
        report_values(change(daily(), path, value))


@pytest.mark.parametrize(
    "ranges",
    [
        # An end before its start, an open interval that is not the last,
        # and two that overlap.
        [{"start": "2026-10-21T13:00:00+00:00", "end": "2026-10-20T13:00:00+00:00"}],
        [
            {"start": "2026-10-20T13:00:00+00:00", "end": None},
            {"start": "2026-10-22T13:00:00+00:00", "end": None},
        ],
        [
            {"start": "2026-10-20T13:00:00+00:00", "end": "2026-10-22T13:00:00+00:00"},
            {"start": "2026-10-21T13:00:00+00:00", "end": None},
        ],
    ],
)
def test_pause_intervals_are_ordered_and_disjoint(ranges):
    """Pause intervals ascend without overlapping, and only the last is open."""
    with pytest.raises(ConfigError):
        report_values(change(weekly(), ("paused_ranges",), ranges))


def test_weekdays_and_chosen_dates_are_ordered():
    """Weekdays ascend without repeats; a chosen Until is not before From."""
    for schedule in (
        {"weekdays": [2, 0]},
        {"weekdays": [0, 0]},
        {"from": "2026-10-20", "until": "2026-10-19"},
    ):
        values = weekly()
        values["schedule"].update(schedule)
        with pytest.raises(ConfigError):
            report_values(values)


def test_names_are_distinct_and_reports_limited():
    """Names compare case-insensitively with spaces collapsed; 20 at most."""
    assert name_key("  Daily   REPORT ") == "daily report"
    validate_reports([daily(), weekly()])
    twin = change(weekly(), ("name",), "daily  Report")
    with pytest.raises(ConfigError):
        validate_reports([daily(), twin])
    many = [change(daily(), ("name",), f"Report {n}") for n in range(21)]
    validate_reports(many[:20])
    with pytest.raises(ConfigError):
        validate_reports(many)


START, END = date(2026, 10, 5), date(2026, 10, 30)  # a Monday and a Friday


@pytest.mark.parametrize(
    ("schedule", "expected"),
    [
        (daily()["schedule"], (date(2026, 10, 6), date(2026, 10, 31))),
        (weekly()["schedule"], (START, date(2026, 11, 2))),
        (
            {
                "frequency": "weekly",
                "weekdays": [4],
                "from": "campaign_start",
                "until": "first_send_after_campaign_end",
            },
            (START, date(2026, 11, 6)),
        ),
        (
            {
                "frequency": "weekly",
                "weekdays": [1, 5],
                "from": "2026-10-01",
                "until": "first_send_after_campaign_end",
            },
            (date(2026, 10, 1), date(2026, 10, 31)),
        ),
        (
            {
                "frequency": "daily",
                "from": "campaign_start",
                "until": "first_send_after_campaign_end",
            },
            (START, date(2026, 10, 31)),
        ),
        (
            {"frequency": "daily", "from": "campaign_start", "until": "campaign_end"},
            (START, END),
        ),
        (
            {"frequency": "daily", "from": "campaign_start", "until": "archive"},
            (START, None),
        ),
        (
            {"frequency": "daily", "from": "campaign_start", "until": "2026-12-01"},
            (START, date(2026, 12, 1)),
        ),
    ],
)
def test_repeat_bounds_follow_the_campaign(schedule, expected):
    """Campaign-relative bounds become send dates (spec, "Repeating sends")."""
    assert repeat_bounds(schedule, START, END) == expected
