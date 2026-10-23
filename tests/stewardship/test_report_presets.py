"""The report emails' starting points (report-emails spec, #2037).

"Campaign wizard starting points": Daily report and Weekly report, defined
once in ``campaigns.report_presets`` and drawn by New report email's Start
from (``report_email_editor.starting_points``).
"""

import pytest

from parishkit.stewardship.accounts.reports_conversion import converted_values
from parishkit.stewardship.campaigns.report_presets import (
    DAILY,
    STARTING_POINTS,
    WEEKLY,
    starting_point,
)
from parishkit.stewardship.campaigns.report_values import report_values
from parishkit.stewardship.reports import report_email_editor as editor

CAMPAIGN = "6f1c5a52-6c3e-4f2f-9a59-0f4b3c1f2e10"
# The converted reports' values the starting points share; the subject,
# introduction, skips and slot basis come from the digest instead.
SHARED = ("name", "recipients", "as_of", "blocks", "content_required")


def timed(values, time="07:00:00"):
    """``values`` with the time a report needs before it is saved."""
    values["schedule"]["time"] = time
    return values


@pytest.mark.parametrize("key", STARTING_POINTS)
def test_each_starting_point_is_valid_once_it_has_a_time(key):
    """A starting point has every stored key but the time, and validates."""
    values = starting_point(key, CAMPAIGN)
    assert "time" not in values["schedule"]
    assert report_values(timed(values)) is values


def test_the_starting_points_are_the_spec_s():
    """Names, subjects, schedules and blocks as the spec lists them."""
    daily, weekly = starting_point(DAILY, CAMPAIGN), starting_point(WEEKLY, CAMPAIGN)
    assert STARTING_POINTS == (DAILY, WEEKLY)
    assert daily["name"] == "Daily report"
    assert daily["subject"] == "{{ campaign_name }} — daily report"
    assert daily["schedule"]["frequency"] == "daily"
    assert daily["as_of"] == "previous_day_end"
    assert [entry["block"] for entry in daily["blocks"]] == [
        "campaign_totals",
        "response_funnel",
        "participation_charts",
        "day_by_day",
    ]
    # All three charts: no "which charts" option; five days.
    assert daily["blocks"][2]["options"] == {}
    assert daily["blocks"][3]["options"] == {"days": 5}
    assert daily["content_required"] is False
    assert weekly["name"] == "Weekly report"
    assert weekly["subject"] == "{{ campaign_name }} — weekly report"
    assert weekly["schedule"]["weekdays"] == [0]
    assert weekly["as_of"] == "send_time"
    assert [entry["block"] for entry in weekly["blocks"]] == [
        "additional_information",
        "response_funnel",
    ]
    assert weekly["content_required"] is True
    for values in (daily, weekly):
        assert values["recipients"] == [{"group": "administrators"}]
        assert (values["intro_html"], values["intro_text"]) == ("", "")
        assert values["slot_basis"] == "send_date"
        assert values["campaign_id"] == CAMPAIGN


@pytest.mark.parametrize(
    ("key", "digest"),
    [
        (DAILY, {"kind": "daily_digest"}),
        (WEEKLY, {"kind": "weekly_digest", "weekday": 0}),
    ],
)
def test_each_starting_point_is_the_converted_report(key, digest):
    """The converted digest's values, with no time chosen (spec)."""
    converted = converted_values(
        digest
        | {
            "campaign_id": CAMPAIGN,
            "time": "07:00:00",
            "subject": "",
            "skip_dates": [],
        },
        None,
    )
    values = starting_point(key, CAMPAIGN)
    assert {name: values[name] for name in SHARED} == {
        name: converted[name] for name in SHARED
    }
    del converted["schedule"]["time"]
    assert values["schedule"] == converted["schedule"]


def test_each_call_returns_a_fresh_copy():
    """A caller's change never reaches the next caller's starting point."""
    first = starting_point(WEEKLY, CAMPAIGN)
    first["schedule"]["weekdays"].append(3)
    first["blocks"][0]["options"]["x"] = 1
    again = starting_point(WEEKLY, CAMPAIGN)
    assert again["schedule"]["weekdays"] == [0]
    assert again["blocks"][0]["options"] == {}


def test_start_from_draws_blank_and_each_starting_point():
    """New's Start from: Blank's fields are New's, the others the presets'."""
    choices = editor.recipient_choices([])
    points = editor.starting_points({"modules": []}, CAMPAIGN, choices)
    assert [point["key"] for point in points] == ["blank", DAILY, WEEKLY]
    blank, daily, weekly = points
    assert blank["fields"] == editor.START_DEFAULTS | editor.initial(None, {}, None)
    assert (blank["recipients"], blank["blocks"]) == ([], [])
    for point in (daily, weekly):
        fields = point["fields"]
        # Every field Start from sets, the time left empty, no None.
        assert set(fields) == set(editor.START_DEFAULTS)
        assert fields["time"] == ""
        assert None not in fields.values()
        assert (fields["sends"], fields["editor_mode"]) == ("repeat", "basic")
        assert point["recipients"] == [
            {
                "value": "group:administrators",
                "label": editor.chip_label("group:administrators", choices),
            }
        ]
        values = starting_point(point["key"], CAMPAIGN)
        assert point["label"] == values["name"]
        assert point["blocks"] == [entry["block"] for entry in values["blocks"]]
        assert fields["name"] == values["name"]
        assert fields["subject"] == values["subject"]
        assert fields["as_of"] == values["as_of"]
        assert fields["content_required"] is values["content_required"]
    assert daily["fields"]["preset"] == "daily"
    assert daily["fields"]["days_shown"] == 5
    assert weekly["fields"]["preset"] == "weekly"
    assert weekly["fields"]["preset_weekday"] == "0"
    assert weekly["fields"]["weekdays"] == ["0"]
    # The charts the campaign offers: no pledges without financial
    # stewardship, all three with it.
    assert daily["charts"] == editor.offered_charts({"modules": []})
    financial = editor.starting_points({"modules": ["financial"]}, CAMPAIGN, choices)
    assert len(financial[1]["charts"]) == 3
