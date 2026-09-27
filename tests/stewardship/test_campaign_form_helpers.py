"""Campaign and schedule form helpers: period ends, overlap, saved emails."""

import pytest

from parishkit.stewardship.accounts.campaign_forms import CampaignForm, overlaps

FINANCIAL = {
    "name": "Renewal",
    "timezone": "America/New_York",
    "start_date": "2026-10-01",
    "end_date": "2026-10-31",
    "financial_enabled": "on",
    "financial_start": "2027-01-01",
    "financial_end": "2027-12-31",
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
        (("2026-10-01", "2026-10-31", "2027-01-01", "2027-12-31"), False),
        (("2026-10-01", "2026-10-31", "2026-10-31", "2027-10-30"), True),
        (("2026-10-01", "2026-10-31", "2025-10-01", "2026-10-01"), True),
        (("2026-10-01", "2026-10-31", "2025-10-01", "2026-09-30"), False),
        (("2026-10-01", "", "2026-10-15", "2027-10-14"), False),
        (("2026-10-01", "2026-10-31", "not a date", "2027-10-14"), False),
    ],
)
def test_overlap_rule_matches_the_inclusive_campaign_check(dates, expected):
    """Same-day edges overlap; incomplete dates never ask for confirmation."""
    assert overlaps(*dates) is expected


def test_confirmation_is_hidden_until_the_dates_overlap():
    """Without JavaScript the field still appears whenever the server needs it."""
    form = bound()
    assert not form.overlap_needed
    html = str(form["overlap_confirmed"].as_field_group())
    assert "data-overlap-confirmation" in html and " hidden>" in html
    assert 'data-campaign-start-name="start_date"' in html
    assert 'data-period-end-name="financial_end"' in html
    assert form.is_valid(), form.errors


def test_unconfirmed_overlap_is_an_inline_error_on_a_visible_checkbox():
    """The re-rendered form shows the checkbox with its own error."""
    form = bound(financial_start="2026-07-01", financial_end="2027-06-30")
    assert form.overlap_needed and not form.is_valid()
    assert "overlaps the campaign" in str(form.errors["overlap_confirmed"])
    html = str(form["overlap_confirmed"].as_field_group())
    assert " hidden>" not in html
    confirmed = bound(
        financial_start="2026-07-01", financial_end="2027-06-30", overlap_confirmed="on"
    )
    assert confirmed.is_valid(), confirmed.errors


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


def test_schedule_window_asks_for_confirmation_only_when_dates_overlap():
    """The window's confirmation follows the fixed financial period (2027)."""
    assert not window().overlap_needed
    html = str(window()["overlap_confirmed"].as_field_group())
    assert " hidden>" in html
    assert 'data-period-start-value="2027-01-01"' in html
    assert 'data-campaign-end-name="window-end_date"' in html
    dates = {
        "timezone": "America/New_York",
        "start_date": "2026-10-01",
        "end_date": "2027-01-05",
    }
    unconfirmed = window(**dates)
    assert unconfirmed.overlap_needed and not unconfirmed.is_valid()
    assert "overlap the upcoming financial period" in str(
        unconfirmed.errors["overlap_confirmed"]
    )
    assert " hidden>" not in str(unconfirmed["overlap_confirmed"].as_field_group())
    assert window(**dates, overlap_confirmed="on").is_valid()


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
