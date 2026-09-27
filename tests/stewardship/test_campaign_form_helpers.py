"""Campaign and schedule form helpers: period ends, overlap, saved emails."""

from parishkit.stewardship.accounts.campaign_forms import CampaignForm


def test_period_start_fields_name_the_end_they_fill():
    """The page script fills an empty end date from the start date."""
    form = CampaignForm()
    assert 'data-fills-end="financial_end"' in str(form["financial_start"])
    assert 'data-fills-end="comparison_end"' in str(form["comparison_start"])
    assert "Ctrl" in str(form.fields["fund_duids"].help_text)
