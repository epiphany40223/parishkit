"""Synthetic directory data rendered with production templates and native forms."""

from datetime import UTC, datetime
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.directories import REACH, REASONS, DirectoryQuery
from parishkit.stewardship.reports.directory_documents import export_headings
from parishkit.stewardship.source.family_names import family_heads_name


def components(context, admin):
    """Browser tests share the existing server/process pool; no provider is used."""
    campaign = UUID(int=80)
    query = DirectoryQuery(search="Example")
    heads = [{"name": "Example Head"}]
    values = {
        "campaign_id": campaign,
        "metadata": {
            "name": "Sample campaign",
            "source_generation": 1234,
            "source_as_of": datetime(2026, 9, 19, tzinfo=UTC),
        },
        "rows": [
            {
                "family_name": "Example <Family>",
                "display_name": family_heads_name("Example <Family>", heads),
                "family_duid": 12345,
                "code": "ABCDEFGH",
                "reason_label": REASONS["provider_refused"],
                "email_eligible": True,
                "email_deliverable": False,
                "responded": True,
                "envelope": "0123",
                "heads": heads,
                "phones": [
                    {"owner": "Example Head", "kind": "home", "value": "202-555-0123"}
                ],
                "address": {
                    "primaryAddress1": "1 Example Street",
                    "primaryAddress2": "Apt 2",
                    "primaryCity": "Town",
                    "primaryState": "KY",
                    "primaryPostalCode": "40000",
                },
                "address_lines": ("1 Example Street", "Apt 2", "Town KY 40000"),
                "mailable": True,
                "addressee": "Example Head",
            }
        ],
        "query": query,
        "query_fields": query.form_values(),
        "reasons": REASONS,
        "reaches": REACH,
        "report_url": f"/admin/reports/{campaign}/families/",
        "total": 51,
        "next_page": 2,
        "postal_proportion": "51 out of 1,000 (5.1%)",
        "mutable": True,
        "request_key": UUID(int=81),
        "export_timezones": ("UTC", "America/Detroit"),
    }
    # The one Family directory page without and with its mailing columns.
    code_list = {
        "mailing": False,
        "query_fields": query.form_values() | {"mailing": "no"},
        "export_headings": export_headings(postal=False, reach="any"),
    }
    mailing = {
        "mailing": True,
        "query_fields": query.form_values() | {"mailing": "yes"},
        "export_headings": export_headings(postal=True, reach="any"),
        "unreachable_total": 2,
        "unreachable_url": f"/admin/reports/{campaign}/families/?reach=neither",
    }
    pages = {
        "/directory-gated": values | code_list | {"mutable": False},
        "/family-directory": values | code_list,
        "/postal-directory": values | mailing,
        "/directory-empty": values
        | mailing
        | {"rows": [], "total": 0, "next_page": None},
    }
    return {
        path: (
            "text/html",
            render_to_string(
                "stewardship/directory.html", context | {"admin_chrome": admin} | data
            ),
        )
        for path, data in pages.items()
    }
