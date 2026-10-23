"""Dates and mail schedules' suggested report emails (#2038).

``suggested_reports`` offers the report emails' starting points beside the
suggested Family schedules (report-emails spec, "Suggested report
emails"): each not yet named by a report of the campaign, ticked when
drawn, with a time required only while ticked. A ticked row becomes a
``report-email preview`` ``create`` entry whose values are the starting
point's with the typed time in the campaign's zone.
"""

from datetime import UTC, datetime

import pytest
from django.http import QueryDict

from parishkit.stewardship.accounts import suggested_reports
from parishkit.stewardship.campaigns.report_presets import (
    DAILY,
    WEEKLY,
    starting_point,
)
from parishkit.stewardship.campaigns.report_values import report_values
from parishkit.stewardship.reports.report_email_changes import report_of

from .campaign_factory import campaign

OWNER = "00000000-0000-4000-8000-000000000001"
NOW = datetime(2054, 9, 1, tzinfo=UTC)
VALUES = campaign()["values"]
KEYS = [DAILY, WEEKLY]


def form(data, keys=KEYS, values=VALUES):
    """The suggested report emails' form, bound to ``data`` (a dict)."""
    posted = QueryDict(mutable=True)
    for name, value in (data or {}).items():
        posted[name] = value
    return suggested_reports.SuggestedReports(
        None if data is None else posted,
        keys=keys,
        campaign=values,
        campaign_id=OWNER,
        now=NOW,
    )


def test_both_starting_points_are_offered_until_a_report_has_the_name():
    """A starting point a saved report already names is left out."""
    assert suggested_reports.offered([], OWNER) == [DAILY, WEEKLY]
    # Names compare as New report email compares them: case and spaces.
    assert suggested_reports.offered([{"name": "daily  REPORT"}], OWNER) == [WEEKLY]
    both = [{"name": "Daily report"}, {"name": "Weekly report"}]
    assert suggested_reports.offered(both, OWNER) == []


def test_the_rows_are_ticked_when_drawn_and_say_what_each_sends():
    """Unbound, each row is ticked with an empty time, in plain words."""
    daily, weekly = suggested_reports.rows(form(None))
    assert [daily["name"], weekly["name"]] == ["Daily report", "Weekly report"]
    assert daily["tick"].value() is True and not daily["time"].value()
    assert daily["rule"] == (
        "Every day, from the day after the campaign starts until the day after "
        "it ends. It keeps sending after the campaign's last day."
    )
    assert weekly["rule"] == (
        "Every Monday, from the campaign's first day until the first send "
        "after it ends. It keeps sending after the campaign's last day."
    )
    assert daily["recipients"] == "All Administrators"
    assert daily["blocks"].startswith("Campaign totals, Response funnel, ")
    assert daily["blocks"].endswith("Day by day table (5 days)")
    assert weekly["blocks"] == "Open additional information requests, Response funnel"
    assert daily["tick"].html_name == "report_daily"
    assert daily["time"].auto_id == "suggested-report_daily_time"


def test_nothing_ticked_adds_no_report():
    """Unticked rows add nothing, and their times are ignored."""
    unticked = form({"zone": "America/Chicago", "report_daily_time": "25:99"})
    assert unticked.is_valid(), unticked.errors
    assert unticked.entries() == []


def test_a_ticked_report_without_a_time_is_refused_at_its_time_field():
    """Each ticked row needs its own time; the unticked one asks nothing."""
    refused = form(
        {"zone": "America/Chicago", "report_daily": "yes", "report_weekly": ""}
    )
    assert not refused.is_valid()
    assert list(refused.errors) == ["report_daily_time"]
    assert refused.errors.as_data()["report_daily_time"][0].code == "time_missing"
    assert refused.entries() == []
    unreadable = form(
        {
            "zone": "America/Chicago",
            "report_daily": "yes",
            "report_daily_time": "half past",
        }
    )
    assert not unreadable.is_valid()
    assert list(unreadable.errors) == ["report_daily_time"]


def test_a_time_without_a_browser_zone_is_refused():
    """The time cannot be read without the browser's zone."""
    refused = form({"report_weekly": "yes", "report_weekly_time": "8:00 AM"})
    assert not refused.is_valid()
    assert refused.errors.as_data()["report_weekly_time"][0].code == "zone_missing"


@pytest.mark.parametrize(
    ("zone", "typed", "stored"),
    [
        # The parish's own zone: stored as typed.
        (VALUES["timezone"], "7:00 AM", "07:00:00"),
        # An hour behind the parish (Central against Eastern).
        ("America/Chicago", "7:00 AM", "08:00:00"),
    ],
)
def test_each_ticked_report_is_its_starting_point_with_the_time(zone, typed, stored):
    """A create entry per ticked row: the starting point plus the time.

    The time is typed in the browser's zone and stored in the campaign's;
    the entry makes valid report values (``report_of``, as the
    ``report-email preview`` command builds them).
    """
    assert VALUES["timezone"] == "America/New_York"
    ticked = form(
        {
            "zone": zone,
            "report_daily": "yes",
            "report_daily_time": typed,
            "report_weekly": "yes",
            "report_weekly_time": typed,
        }
    )
    assert ticked.is_valid(), ticked.errors
    daily, weekly = ticked.entries()
    for entry, key in ((daily, DAILY), (weekly, WEEKLY)):
        assert entry["operation"] == "create"
        expected = starting_point(key, OWNER)
        expected["schedule"]["time"] = stored
        assert report_values(report_of(entry["values"], None, OWNER)) == expected


def test_only_the_offered_keys_are_read():
    """A form offering only the Weekly report reads only its fields."""
    assert suggested_reports.fields([WEEKLY]) == {
        "zone",
        "report_weekly",
        "report_weekly_time",
    }
    weekly = form({"zone": "America/New_York", "report_weekly": "yes"}, keys=[WEEKLY])
    assert not weekly.is_valid() and list(weekly.errors) == ["report_weekly_time"]


def test_a_one_time_send_time_is_refused_not_saved_unchecked(monkeypatch):
    """``_report_rows`` refuses a preset that yields one-time send times.

    Apply signs those so a passed time is refused (#1464); the wizard's
    save signs none, so a future one-time starting point must not slip by.
    """
    from parishkit.stewardship.accounts import schedule_entry_views
    from parishkit.stewardship.reports import (
        report_email_changes,
        report_email_editor,
        report_email_list,
    )

    monkeypatch.setattr(
        report_email_changes, "change_rows", lambda *a, **k: ([], [NOW], [])
    )
    monkeypatch.setattr(schedule_entry_views, "database_now", lambda: NOW)
    monkeypatch.setattr(report_email_editor, "portal_users", lambda c: ())
    monkeypatch.setattr(report_email_list, "report_records", lambda c, pk: [])
    config = type("C", (), {})()
    camp = type("K", (), {"pk": 1})()
    camp.active_configuration = type("A", (), {"values": VALUES})()
    with pytest.raises(RuntimeError, match="one-time"):
        schedule_entry_views._report_rows(config, camp, [{"operation": "create"}])
