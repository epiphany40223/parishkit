"""Ministry reports reuse the shared browser server and production templates.

Ministry requests is the campaign-wide summary only: one Ministry's Members
joining and leaving lists were retired (#1157).
"""

from datetime import UTC, datetime
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports.chart_specs import ministry_followup
from parishkit.stewardship.reports.ministries import (
    SUMMARY_SORTING,
    MinistryQuery,
    summary_totals,
)
from parishkit.stewardship.reports.ministry_views import queue_links
from parishkit.stewardship.web.tables import report_table


def _table(rows, query, total, *, action):
    """The shared POST navigator/heading model the view builds (#203)."""
    return report_table(
        rows,
        number=1,
        size=50,
        total=total,
        carry=[(k, v) for k, v in query.form_values().items() if k != "sort"],
        sorting=SUMMARY_SORTING,
        sort=query.sort,
        action=action,
    )


def components(context, admin):
    """Detached authorized sample data exercises native controls and escaping."""
    campaign = UUID(int=90)
    moment = datetime(2026, 9, 19, tzinfo=UTC)
    root = reverse("admin:ministry_report")
    query = MinistryQuery(search="Example")
    first = [
        dict(
            duid=9,
            name="Example <Ministry>",
            # Not in the latest ParishSoft data, so Staff see that mark.
            active=None,
            joining=51,
            leaving=0,
            unresolved=40,
            completed=11,
            requests=51,
            progress="11 out of 51 (21.6%)",
        )
    ]
    # The packet offers every Ministry in scope, whatever the table shows
    # (#533): here a second Ministry the first summary page leaves out.
    summaries = [dict(first[0], duid=10, name="Another Ministry"), *first]
    # Each count's link to the follow-up queue (#1133), as the view builds
    # it for a reader who may follow up every Ministry.
    staff = Principal(UUID(int=97), frozenset({"staff"}))
    for summary in summaries:
        summary["queue_links"] = queue_links(staff, summary)
    # The totals and follow-up chart of every matching Ministry (#720).
    totals = summary_totals(first)
    summary = dict(
        totals=totals,
        followup_chart=ministry_followup(totals),
        # Staff or an Administrator: the ParishSoft and campaign marks (#1135).
        show_marks=True,
        campaign_id=campaign,
        query=query,
        mutable=True,
        packet_key=UUID(int=96),
        report_url=root,
        total=1,
        metadata=dict(
            name="Sample campaign",
            source_generation=1234,
            source_as_of=moment,
            report_date="2026-09-19",
            timezone="America/Detroit",
        ),
        summaries=first,
        packet_choices=[
            {"duid": row["duid"], "name": row["name"]} for row in summaries
        ],
        packet_limit=200,
        table=_table(first, query, 1, action=root),
    )
    # The summary by Ministry name the other way, with the second Ministry
    # on the page: what its heading's POST form returns, served from a GET
    # path to the in-place re-sort tests (#478).
    descending = MinistryQuery(search="Example", sort="name_desc")
    # A search matching a second Ministry with 9 resolved leave requests.
    searched = summary_totals(
        [
            *first,
            dict(first[0], joining=0, leaving=9, unresolved=0, completed=9, requests=9),
        ]
    )
    pages = {
        "/ministry-summary": summary,
        # What a Ministry leader sees: no marks (#1135).
        "/ministry-summary-leader": summary | dict(show_marks=False),
        "/ministry-summary-desc": summary
        | dict(
            query=descending,
            summaries=summaries,
            total=2,
            table=_table(summaries, descending, 2, action=root),
        ),
        "/ministry-gated": summary | dict(mutable=False),
        # What an applied search returns in place (#720): fresh totals and
        # a fresh follow-up chart for the Ministries it matches.
        "/ministry-summary-searched": summary
        | dict(
            totals=searched,
            followup_chart=ministry_followup(searched),
        ),
    }
    return {
        path: (
            "text/html",
            render_to_string(
                "stewardship/ministry-report.html",
                context | {"admin_chrome": admin} | data,
            ),
        )
        for path, data in pages.items()
    }
