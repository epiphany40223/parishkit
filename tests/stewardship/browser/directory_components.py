"""Synthetic directory data rendered with production templates and native forms."""

from datetime import UTC, datetime
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.directories import REASONS, DirectoryQuery


def components(context, admin):
    """Browser tests share the existing server/process pool; no provider is used."""
    campaign = UUID(int=80)
    query = DirectoryQuery(search="Example")
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
                "family_duid": 12345,
                "code": "ABCDEFGH",
                "reason_label": REASONS["provider_refused"],
                "email_eligible": True,
                "email_deliverable": False,
                "responded": True,
                "envelope": "0123",
                "heads": [{"name": "Example Head"}],
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
            }
        ],
        "query": query,
        "query_fields": query.form_values(),
        "reasons": REASONS,
        "total": 51,
        "next_page": 2,
        "postal_proportion": "51 out of 1,000 (5.1%)",
        "mutable": True,
        "request_key": UUID(int=81),
        "export_timezones": ("UTC", "America/Detroit"),
    }
    pages = {
        "/directory-gated": values
        | {
            "mutable": False,
            "postal": False,
            "report_url": f"/admin/reports/{campaign}/families/",
        },
        "/family-directory": values
        | {
            "postal": False,
            "report_url": f"/admin/reports/{campaign}/families/",
        },
        "/postal-directory": values
        | {
            "postal": True,
            "report_url": f"/admin/reports/{campaign}/postal/",
        },
        "/directory-empty": values
        | {
            "rows": [],
            "total": 0,
            "next_page": None,
            "postal": True,
            "report_url": f"/admin/reports/{campaign}/postal/",
        },
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
