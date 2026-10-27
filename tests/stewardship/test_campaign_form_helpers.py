"""Campaign and schedule form helpers: period ends, the commitment period's
start, saved emails."""

import pytest

from parishkit.stewardship.accounts.campaign_forms import (
    COMMITMENT_AFTER_END,
    CampaignForm,
    starts_after_campaign,
)

FINANCIAL = {
    "name": "Renewal",
    "timezone": "America/New_York",
    "start_date": "2054-10-01",
    "end_date": "2054-10-31",
    "financial_enabled": "on",
    "financial_start": "2055-01-01",
    "financial_end": "2055-12-31",
    "comparison_start": "2026-01-01",
    "comparison_end": "2026-12-31",
    "fund_duids": ["9"],
    "comparison_fund_duids": ["9"],
    "base_digest": "0" * 64,
}


def bound(**changes):
    """A bound campaign form with one fund in its catalog."""
    return CampaignForm(FINANCIAL | changes, funds=[("9", "Offertory")])


@pytest.mark.parametrize(
    ("dates", "expected"),
    [
        (("2054-10-31", "2055-01-01"), True),
        (("2054-10-31", "2054-11-01"), True),
        (("2054-10-31", "2054-10-31"), False),
        (("2054-10-31", "2053-10-01"), False),
        (("", "2054-10-15"), True),
        (("2054-10-31", "not a date"), True),
    ],
)
def test_the_commitment_period_starts_after_the_campaign_ends(dates, expected):
    """The day after the end is the earliest start (#2293); incomplete or
    invalid dates are left to their own checks."""
    assert starts_after_campaign(*dates) is expected


def test_a_commitment_period_on_or_before_the_end_is_refused_at_its_start():
    """The refusal sits at the start field, names the first allowed day and
    carries its closed code; the overlap confirmation it replaced is gone."""
    for start, end in (("2054-10-31", "2055-10-30"), ("2054-07-01", "2055-06-30")):
        form = bound(financial_start=start, financial_end=end)
        assert not form.is_valid()
        assert form.errors["financial_start"] == [
            "The commitment period must start after the campaign ends: choose "
            "November 1, 2054 or later."
        ]
        assert form.has_error("financial_start", COMMITMENT_AFTER_END)
    assert "overlap_confirmed" not in bound().fields
    form = bound(financial_start="2054-11-01", financial_end="2055-10-31")
    assert form.is_valid(), form.errors
    # A new period stores no confirmation; a saved one keeps its answer.
    assert form.values()["financial"]["overlap_confirmed"] is False
    kept = CampaignForm(
        FINANCIAL,
        funds=[("9", "Offertory")],
        previous={"modules": ["financial"], "financial": {"overlap_confirmed": True}},
    )
    assert kept.is_valid(), kept.errors
    assert kept.values()["financial"]["overlap_confirmed"] is True


def test_a_saved_overlap_is_judged_only_when_its_dates_change():
    """A campaign saved before #2293 with a confirmed overlap still saves
    its other settings, keeping its answer; moving the campaign end or the
    commitment period start is judged, and refused while they overlap."""
    overlap = {"financial_start": "2054-10-15", "financial_end": "2055-10-14"}
    previous = {
        "modules": ["financial"],
        "end_date": "2054-10-31",
        "financial": {"start": "2054-10-15", "overlap_confirmed": True},
    }
    form = CampaignForm(
        FINANCIAL | overlap | {"name": "Renamed"},
        funds=[("9", "Offertory")],
        previous=previous,
    )
    assert form.is_valid(), form.errors
    assert form.values()["financial"]["overlap_confirmed"] is True
    for changes in (
        {"end_date": "2054-10-30"},
        {"financial_start": "2054-10-16", "financial_end": "2055-10-15"},
    ):
        moved = CampaignForm(
            FINANCIAL | overlap | changes,
            funds=[("9", "Offertory")],
            previous=previous,
        )
        assert moved.has_error("financial_start", COMMITMENT_AFTER_END)


def test_the_commitment_start_names_the_campaign_end_as_its_minimum():
    """The page script holds a start on or before the campaign end
    (data-not-before with data-strictly-after: the day after the end is its
    min), as the server refuses the end's own day too."""
    html = str(CampaignForm()["financial_start"])
    assert 'data-not-before="id_end_date"' in html
    assert " data-strictly-after" in html
    assert (
        'data-missing-hint="Choose a commitment period start after the campaign '
        'end date."' in html
    )


def test_period_start_fields_name_the_end_they_fill():
    """The page script fills an empty end date from the start date."""
    form = CampaignForm()
    assert 'data-fills-end="financial_end"' in str(form["financial_start"])
    assert 'data-fills-end="comparison_end"' in str(form["comparison_start"])
    assert "Ctrl" in str(form.fields["fund_duids"].help_text)


def window(**data):
    """A schedules-page campaign window over a financial campaign."""
    from parishkit.stewardship.accounts.schedule_forms import ScheduleWindow

    from .campaign_factory import campaign, financial

    values = campaign(modules=["financial"], financial=financial())["values"]
    bound = {f"window-{name}": value for name, value in data.items()}
    return ScheduleWindow(
        bound or None, prefix="window", previous=values, editable=True
    )


def test_schedule_window_ends_before_the_commitment_period():
    """The window's end must come before the fixed commitment period (2055):
    an end on or after its start is refused at the end date, with its code,
    and the overlap acknowledgement is gone."""
    assert "overlap_confirmed" not in window().fields
    dates = {"timezone": "America/New_York", "start_date": "2054-10-01"}
    for end in ("2055-01-01", "2055-01-05"):
        refused = window(**dates, end_date=end)
        assert not refused.is_valid()
        assert refused.errors["end_date"] == [
            "The campaign must end before its commitment period starts on "
            "January 1, 2055: choose an earlier end date."
        ]
        assert refused.has_error("end_date", COMMITMENT_AFTER_END)
    accepted = window(**dates, end_date="2054-12-31")
    assert accepted.is_valid(), accepted.errors
    assert accepted.values()["financial"]["overlap_confirmed"] is False


def test_schedules_without_a_saved_email_explain_what_to_do_first():
    """An empty template list is explained instead of offered."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.accounts.schedule_forms import Schedules

    from .campaign_factory import campaign

    owner = campaign()
    schedules = Schedules(
        prefix="schedules",
        previous=[],
        templates=[],
        campaign_id=owner["id"],
        campaign=owner["values"],
    )
    assert not schedules.has_templates
    assert "No invitation" in str(
        schedules.forms[0].fields["template_version"].help_text
    )
    html = render_to_string(
        "stewardship/schedule-fields.html",
        {"schedules": schedules, "templates_url": "/admin/setup/content"},
    )
    assert "No invitation or reminder emails are saved yet." in html
    assert 'href="/admin/setup/content"' in html
