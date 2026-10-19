"""A send date inside the campaign: the server's rule and the page's copy (#1787).

The shared case table (``fixtures/schedule_window_cases.json``) is run here
through the date-change review's own formset, with times typed in the
browser's zone, and through the page script in every engine by
``browser/test_schedule_window.py``, so the two sides cannot drift apart.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from parishkit.stewardship.accounts import schedule_local
from parishkit.stewardship.accounts.schedule_forms import (
    OUTSIDE_CAMPAIGN,
    OUTSIDE_WINDOW,
    Schedules,
    saved_send_attrs,
    send_window_attrs,
)

from .campaign_factory import campaign, schedule
from .content_factory import content
from .test_schedule_forms import data_for

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "schedule_window_cases.json").read_text()
)["cases"]
NOW = datetime(2054, 1, 1, 12, tzinfo=UTC)


def judged(case):
    """Run one case through the review's formset; return the row's error codes.

    The campaign's saved zone is the saved row's (or the window's), and the
    window (dates and zone) replaces the formset's campaign before it is
    validated, as ``schedule_changes.build_preview`` does with proposed
    dates. The row is a reminder: the case's saved one, or a new one.
    """
    start, end, zone = case["window"]
    saved = case.get("saved")
    owner = campaign()
    owner["values"]["timezone"] = saved["zone"] if saved else zone
    email = content(owner["id"], kind="email", slot="reminder", subject="Later")
    previous = []
    if saved:
        previous = [
            schedule(
                owner["id"],
                kind="reminder",
                date=saved["date"],
                time=saved["time"],
                template_version=email["id"],
                subject="Later",
            )
        ]
    data = data_for(previous) | {
        "zone": case["browser"],
        "schedules-0-kind": "reminder",
        "schedules-0-date": case["date"],
        "schedules-0-time": case["time"],
        "schedules-0-template_version": email["id"],
    }
    formset = Schedules(
        data,
        prefix="schedules",
        previous=previous,
        templates=[email],
        campaign_id=owner["id"],
        campaign=owner["values"],
        browser_times=True,
        zone=case["browser"],
        now=NOW,
    )
    formset.campaign = owner["values"] | {
        "start_date": start,
        "end_date": end,
        "timezone": zone,
    }
    formset.is_valid()
    return {
        error.code
        for errors in formset.forms[0].errors.as_data().values()
        for error in errors
    }


@pytest.mark.parametrize("case", CASES, ids=[case["why"][:60] for case in CASES])
def test_the_server_judges_every_shared_case(case):
    """The formset admits exactly the table's inside dates, at the date.

    Nothing else may be refused on the row (a repeated hour, a missing
    email), so each case tests the window rule alone.
    """
    codes = judged(case)
    assert codes <= {OUTSIDE_CAMPAIGN}
    assert (OUTSIDE_CAMPAIGN not in codes) == case["inside"]


def test_the_table_covers_both_verdicts_and_saved_rows():
    """Each kind of case the page script must agree on is in the table."""
    assert {case["inside"] for case in CASES} == {True, False}
    saved = [case for case in CASES if case.get("saved")]
    assert {case["inside"] for case in saved} == {True, False}
    # A proposed zone differs from the saved row's own.
    assert any(case["window"][2] != case["saved"]["zone"] for case in saved)
    assert len({case["browser"] for case in CASES}) >= 6


def test_browser_time_dates_carry_the_window_check():
    """The send date names the check, its words and its reserved line.

    A saved dated row also carries its saved date and the moment the page
    shows it at, bound or not; a digest has none, and pages that keep
    campaign times (the retired Copy campaign) draw no check.
    """
    owner = campaign()
    dated = schedule(owner["id"], date="2054-10-10")
    digest = schedule(owner["id"], kind="weekly_digest", date=None, weekday=0)

    def forms(data=None, **extra):
        """The formset's forms for the two saved rows."""
        return Schedules(
            data,
            prefix="schedules",
            previous=[dated, digest],
            templates=[],
            campaign_id=owner["id"],
            campaign=owner["values"],
            **extra,
        ).forms

    browser = {"browser_times": True, "now": NOW}
    for form in (
        forms(**browser)[0],
        forms(data_for([dated, digest]), zone="UTC", **browser)[0],
    ):
        attrs = form.fields["date"].widget.attrs
        assert attrs["data-send-window"] == ""
        assert attrs["data-window-message"] == str(OUTSIDE_WINDOW) % {
            "window": "{window}"
        }
        assert attrs["data-saved-date"] == "2054-10-10"
        assert attrs["data-saved-at"] == "2054-10-10T13:00:00+00:00"
        line = f'id="{form["date"].auto_id}_error"'
        assert line in str(form["date"].as_field_group())
    unsaved = forms(**browser)
    assert "data-saved-date" not in unsaved[1].fields["date"].widget.attrs
    assert "data-saved-date" not in unsaved[2].fields["date"].widget.attrs
    assert "data-send-window" in unsaved[2].fields["date"].widget.attrs
    assert "data-send-window" not in forms()[0].fields["date"].widget.attrs


def test_saved_send_attrs_name_only_dated_sends():
    """A dated send carries its saved date and moment; a digest nothing."""
    values = {"date": "2054-10-10", "time": "09:00:00"}
    moment = schedule_local.instant(values, "America/New_York", None).isoformat()
    assert saved_send_attrs(values, moment) == {
        "data-saved-date": "2054-10-10",
        "data-saved-at": "2054-10-10T13:00:00+00:00",
    }
    assert saved_send_attrs({"date": None, "time": "09:00:00"}, moment) == {}
    assert "{window}" in send_window_attrs()["data-window-message"]
