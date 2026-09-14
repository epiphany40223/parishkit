"""Selected immutable page blocks with explicitly public substitution defaults."""

from datetime import date

from django.db.models import Q

from parishkit.stewardship.accounts.content_forms import LEGACY_PAGE_REFERENCES
from parishkit.stewardship.accounts.content_models import ContentVersion
from parishkit.stewardship.web.content import (
    PLACEHOLDERS,
    render_template,
    validate_template,
)
from parishkit.stewardship.web.presentation import campaign_year, parish_date


def public_substitutions(parish, campaign):
    """Never look up a Family or credential when rendering a public campaign page."""
    return public_values(
        {"name": parish.name, "website": parish.website, "phone": parish.phone},
        campaign.values,
    )


def public_values(parish, campaign):
    """Pure public values shared by page rendering and concurrency projection."""
    values = dict.fromkeys(PLACEHOLDERS, "")
    values.update(
        parish_name=parish["name"],
        parish_website=parish["website"],
        parish_phone=parish["phone"],
        campaign_name=campaign["name"],
        campaign_start=parish_date(date.fromisoformat(campaign["start_date"])),
        campaign_end=parish_date(date.fromisoformat(campaign["end_date"])),
        campaign_timezone=campaign["timezone"],
        campaign_year=campaign_year(campaign),
        generic_family_url="/",
    )
    return values


def family_page_slots(configuration):
    """The same displayed slots control rendering and substitution dependencies."""
    slots = {"welcome", "review", "thank_you"} | set(configuration["modules"])
    if "census" in configuration["modules"]:
        slots.add("member_census")
    if configuration["additional_information"]:
        slots.add("additional")
    return slots


def public_content_dependencies(document, configuration, campaign_id):
    """Pin only public values actually interpolated into displayed page blocks."""
    if document is None:
        return {}
    sections = document["sections"]
    slots = family_page_slots(configuration)
    selected = configuration["content_versions"].values()
    names = set()
    for record in sections.get("content", []):
        value = record["values"]
        if (
            value["campaign_id"] == str(campaign_id)
            and value["kind"] == "page"
            and value["slot"] in slots
            and (
                value["slot"] not in LEGACY_PAGE_REFERENCES or record["id"] in selected
            )
        ):
            names.update(validate_template(value["html"]))
    if not names:
        return {}
    values = public_values(sections["parish"][0]["values"], configuration)
    return {name: values[name] for name in names}


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
