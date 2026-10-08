"""Synthetic directory data rendered with production templates and native forms."""

from datetime import UTC, datetime
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.directories import (
    DIRECTORY_SORTING,
    REACH,
    REASONS,
    DirectoryQuery,
    head_email_groups,
)
from parishkit.stewardship.reports.directory_documents import export_headings
from parishkit.stewardship.source.family_names import family_heads_name
from parishkit.stewardship.web.tables import report_table


def _table(rows, total, mailing):
    """The shared POST report table the view builds around one SQL page."""
    query = DirectoryQuery(search="Example")
    return report_table(
        rows,
        number=1,
        size=50,
        total=total,
        carry=[
            *(
                (key, value)
                for key, value in query.form_values().items()
                if key != "sort"
            ),
            ("mailing", mailing),
        ],
        sorting=DIRECTORY_SORTING,
        sort=query.sort,
        action=f"/admin/reports/{UUID(int=80)}/families/",
        sizes=(50,),
    )


def components(context, admin):
    """Browser tests share the existing server/process pool; no provider is used."""
    campaign = UUID(int=80)
    query = DirectoryQuery(search="Example")
    heads = [
        {
            "name": "Example Head",
            "emails": [{"value": "head@example.org", "valid": True}],
        }
    ]
    values = {
        "campaign_id": campaign,
        "metadata": {
            "name": "Sample campaign",
            "source_generation": 1234,
            "source_as_of": datetime(2026, 9, 19, tzinfo=UTC),
        },
        "table_rows": [
            {
                "family_name": "Example <Family>",
                "display_name": family_heads_name("Example <Family>", heads),
                "family_duid": 12345,
                # Open form posts its hand-off for this Family (#529).
                "family_id": UUID(int=81),
                "code": "ABCDEFGH",
                "reason_label": REASONS["provider_refused"],
                "email_eligible": True,
                "email_deliverable": False,
                "responded": True,
                "envelope": "0123",
                "heads": heads,
                "head_emails": head_email_groups(heads),
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
        "table": _table(values["table_rows"], 51, "no"),
    }
    mailing = {
        "mailing": True,
        "query_fields": query.form_values() | {"mailing": "yes"},
        "export_headings": export_headings(postal=True, reach="any"),
        "unreachable_total": 2,
        "unreachable_url": f"/admin/reports/{campaign}/families/?reach=neither",
        "table": _table(values["table_rows"], 51, "yes"),
    }
    # Three heads (#604): Anna and Ben share an address (in different case),
    # Anna also has invalid source text, and John has no email.
    heads = [
        {
            "name": "Anna Example",
            "first": "Anna",
            "last": "Example",
            "emails": [
                {"value": "anna@example.org", "valid": True},
                {"value": "not-an-address", "valid": False},
            ],
        },
        {
            "name": "Ben Example",
            "first": "Ben",
            "last": "Example",
            "emails": [{"value": "Anna@Example.org", "valid": True}],
        },
        {"name": "John Example", "first": "John", "last": "Example", "emails": []},
    ]
    head_emails = values["table_rows"][0] | {
        "family_name": "Example",
        "display_name": family_heads_name("Example", heads),
        "heads": heads,
        "head_emails": head_email_groups(heads),
        # The Family name links to its timeline (#590) beside the pane.
        "family_id": UUID(int=82),
    }
    pages = {
        "/directory-head-emails": values
        | code_list
        | {"table": _table([head_emails], 1, "no"), "total": 1},
        "/directory-gated": values | code_list | {"mutable": False},
        "/family-directory": values | code_list,
        "/postal-directory": values | mailing,
        "/directory-empty": values
        | mailing
        | {"table": _table([], 0, "yes"), "total": 0},
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
