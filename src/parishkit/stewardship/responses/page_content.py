"""Selected immutable page blocks with explicitly public substitution defaults."""

from datetime import date

from django.db.models import Q

from parishkit.stewardship.accounts.content_forms import LEGACY_PAGE_REFERENCES
from parishkit.stewardship.accounts.content_models import ContentVersion
from parishkit.stewardship.web.content import PLACEHOLDERS, render_template
from parishkit.stewardship.web.presentation import campaign_year, parish_date


def public_substitutions(parish, campaign):
    """Never look up a Family or credential when rendering a public campaign page."""
    values = dict.fromkeys(PLACEHOLDERS, "")
    values.update(
        parish_name=parish.name,
        parish_website=parish.website,
        parish_phone=parish.phone,
        campaign_name=campaign.values["name"],
        campaign_start=parish_date(date.fromisoformat(campaign.values["start_date"])),
        campaign_end=parish_date(date.fromisoformat(campaign.values["end_date"])),
        campaign_timezone=campaign.timezone,
        campaign_year=campaign_year(campaign.values),
        generic_family_url="/",
    )
    return values


def render_pages(configuration_id, campaign, slots, substitutions):
    """Read only selected page revisions, then escape and sanitize every expansion."""
    return {
        row.slot: render_template(row.html, substitutions, html=True)
        for row in ContentVersion.objects.filter(
            Q(record_id__in=campaign.values["content_versions"].values())
            | ~Q(slot__in=LEGACY_PAGE_REFERENCES),
            configuration_id=configuration_id,
            campaign_id=campaign.record_id,
            kind="page",
            slot__in=slots,
        )
    }
