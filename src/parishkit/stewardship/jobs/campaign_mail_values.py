"""Public parish and campaign substitutions shared by Family and Admin mail."""

from datetime import date

from parishkit.stewardship.web.presentation import campaign_year, parish_date


def campaign_values(*, parish, campaign):
    """Format civil dates without consulting a worker timezone or private Family."""
    financial = campaign["financial"]
    start = parish_date(date.fromisoformat(financial["start"])) if financial else ""
    end = parish_date(date.fromisoformat(financial["end"])) if financial else ""
    return {
        "parish_name": parish["name"],
        "parish_website": parish["website"],
        "parish_phone": parish["phone"],
        "campaign_name": campaign["name"],
        "campaign_start": parish_date(date.fromisoformat(campaign["start_date"])),
        "campaign_end": parish_date(date.fromisoformat(campaign["end_date"])),
        "campaign_timezone": campaign["timezone"],
        "campaign_year": campaign_year(campaign),
        "financial_start": start,
        "financial_end": end,
        "financial_period": f"{start} – {end}" if financial else "",
    }
