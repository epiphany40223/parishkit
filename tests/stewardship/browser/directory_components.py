"""Synthetic directory data rendered with production templates and native forms."""

from datetime import UTC, datetime
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.reports.directories import (
    CHECKS,
    DIRECTORY_SORTING,
    INACTIVE,
    MISSING_NAME,
    REACH,
    REASONS,
    RESPONSES,
    DirectoryQuery,
    head_email_groups,
    response_columns,
)
from parishkit.stewardship.reports.directory_documents import export_headings
from parishkit.stewardship.source.family_names import family_heads_name
from parishkit.stewardship.web.tables import report_table


def _table(rows, total, mailing, query=None):
    """The shared POST report table the view builds around one SQL page."""
    query = query or DirectoryQuery(search="Example")
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
        action=reverse("admin:family_directory"),
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
        "responses": RESPONSES,
        "checks": CHECKS,
        "column_count": 7,
        "report_url": reverse("admin:family_directory"),
        "total": 51,
        "mutable": True,
        "request_key": UUID(int=81),
        "export_timezones": ("UTC", "America/Detroit"),
    }
    # The one directory page without and with its mailing columns.
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
        "column_count": 9,
        "unreachable_url": reverse("admin:family_directory") + "?reach=neither",
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
    # The Response filter with its response columns and a data check
    # (#933): a never-opened Family that followed its link, and one the
    # current ParishSoft data no longer has.
    responding = DirectoryQuery(
        search="Example", response="link-followed", responses="yes", check="anything"
    )
    dates, counts = response_columns("link-followed")
    invited = datetime(2026, 10, 1, 13, 5, tzinfo=UTC)
    listed = values["table_rows"][0] | {
        "family_id": UUID(int=83),
        "active": True,
        "date_cells": [(dates[0], invited), (dates[1], invited)],
        "count_cells": [],
        "checks": ["Envelope number 0"],
    }
    gone = listed | {
        "family_id": UUID(int=84),
        "family_name": None,
        "display_name": MISSING_NAME,
        "family_duid": 777,
        "active": False,
        "reason": "inactive",
        "reason_label": INACTIVE,
        "code": "HGFEDCBA",
        "date_cells": [(dates[0], invited), (dates[1], None)],
        "checks": [],
        "heads": [],
        "head_emails": [],
        "phones": [],
        "address": {},
        "address_lines": (),
        "mailable": False,
    }
    responses = {
        "query": responding,
        "query_fields": responding.form_values() | {"mailing": "no"},
        "metadata": values["metadata"] | {"counted_at": invited},
        "date_columns": dates,
        "count_columns": counts,
        "column_count": 7 + len(dates) + len(counts) + 1,
        "export_headings": export_headings(
            postal=False, reach="any", dates=dates, counts=counts, checks=True
        ),
        "table": _table([listed, gone], 2, "no", responding),
        "total": 2,
    }
    pages = {
        "/directory-responses": values | code_list | responses,
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
