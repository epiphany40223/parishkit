"""Public parish and campaign substitutions shared by Family and Admin mail."""

from datetime import date

from parishkit.stewardship.web import dates
from parishkit.stewardship.web.presentation import campaign_year, parish_date
from parishkit.stewardship.web.presentation import phone as format_phone


def document_parish(document):
    """Return the parish profile plus its public outgoing-mail Reply-to address.

    The ``parish_email`` placeholder means the address Families can write to,
    which is the configured Reply-to of the outgoing email integration rather
    than a separate profile field. A document without that integration (only
    possible before setup completes) yields an empty value.
    """
    sections = document["sections"]
    email = next(
        (
            row["values"]["settings"]["reply_to"]
            for row in sections.get("integrations", [])
            if row["values"]["kind"] == "email"
        ),
        "",
    )
    return sections["parish"][0]["values"] | {"email": email}


def giving_url(parish):
    """The optional online giving page, falling back to the parish website.

    Templates such as the default receipt link to ``online_giving_url``; an
    empty href would silently point at the email or page itself, so a parish
    without a giving page sends Families to its website instead.
    """
    return parish.get("online_giving_url") or parish["website"]


def campaign_values(*, parish, campaign):
    """Format civil dates without consulting a worker timezone or private Family.

    Workers have no request, so the parish's own ``date_format`` is passed
    explicitly; an unset value means the default style.
    """
    style = dates.normalized(parish.get("date_format"))
    financial = campaign["financial"]
    start = (
        parish_date(date.fromisoformat(financial["start"]), style) if financial else ""
    )
    end = parish_date(date.fromisoformat(financial["end"]), style) if financial else ""
    return {
        "parish_name": parish["name"],
        "parish_website": parish["website"],
        "parish_phone": format_phone(parish["phone"]),
        "parish_email": parish.get("email", ""),
        "online_giving_url": giving_url(parish),
        "campaign_name": campaign["name"],
        "campaign_start": parish_date(
            date.fromisoformat(campaign["start_date"]), style
        ),
        "campaign_end": parish_date(date.fromisoformat(campaign["end_date"]), style),
        "campaign_timezone": campaign["timezone"],
        "campaign_year": campaign_year(campaign),
        "financial_start": start,
        "financial_end": end,
        "financial_period": f"{start} – {end}" if financial else "",
    }
