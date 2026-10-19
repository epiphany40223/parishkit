"""The Limitations report: two shared tables on one page, as the view renders them.

The in-place re-sort tests (#478) need a page whose tables must keep each
other's choices. Each fixture path renders the report under one combination
of the two tables' sorts, built through the view's own ``tables`` helper, so
the headings' POST forms carry exactly what the production page would.
"""

from datetime import UTC, datetime
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.reports.limitation_views import summary_tiles, tables
from parishkit.stewardship.reports.limitations import LimitationsQuery

CAMPAIGN = UUID(int=90)
# The report page's own path: what every heading, navigator and filter form
# posts back to, and what the tests route.
PATH = reverse("admin:limitations_report")


def components(context, admin):
    """The report under each sort combination the tests step through."""
    when = datetime(2026, 9, 20, 12, tzinfo=UTC)
    result = {
        "metadata": {"name": "Sample campaign"},
        "summary": {"cannot_serve": 2, "cannot_attend": 2},
        # Default order is by Family: Baker's member before Carter's. By
        # Member name the order reverses.
        "members": [
            {
                "member_name": "Zed Able",
                "family_name": "Baker",
                "family_duid": 2,
                "proposed": False,
                "cannot_serve": True,
                "submitted_at": when,
            },
            {
                "member_name": "Amy Young",
                "family_name": "Carter",
                "family_duid": 1,
                "proposed": True,
                "cannot_serve": True,
                "submitted_at": when,
            },
        ],
        "families": [
            {"family_name": "Baker", "family_duid": 2, "submitted_at": when},
            {"family_name": "Carter", "family_duid": 1, "submitted_at": when},
        ],
    }
    query = LimitationsQuery()

    def page(paging, data=result, chosen=query):
        """Render the report with both tables sorted as ``paging`` says,
        from ``data`` under the filters ``chosen``."""
        members, families = tables(data, chosen, paging, PATH)
        return (
            "text/html",
            render_to_string(
                "stewardship/limitations-report.html",
                context
                | {"admin_chrome": admin}
                | data
                | {
                    "members_table": members,
                    "families_table": families,
                    "summary_tiles": summary_tiles(data["summary"]),
                    "campaign_id": CAMPAIGN,
                    "query": chosen,
                    "query_fields": chosen.form_values(),
                },
            ),
        )

    # No Member or Family listed, and a summary that agrees.
    nobody = result | {
        "summary": {"cannot_serve": 0, "cannot_attend": 0},
        "members": [],
        "families": [],
    }
    return {
        "/limitations-report": page({}),
        "/limitations-report-families-desc": page({"families_sort": "-family"}),
        "/limitations-report-both": page(
            {"families_sort": "-family", "members_sort": "member"}
        ),
        # Nobody has a limitation yet, and a search that matches nobody:
        # the unfiltered and filtered empty states differ (#227).
        "/limitations-report-none": page({}, nobody),
        "/limitations-report-none-searched": page(
            {}, nobody, LimitationsQuery(search="Nobody")
        ),
        # What Show "Cannot participate in ministries" returns (#484), here
        # narrowed as a search would be: one Member, one Family and a smaller
        # summary.
        "/limitations-report-cannot-serve": page(
            {},
            result
            | {
                "summary": {"cannot_serve": 1, "cannot_attend": 1},
                "members": result["members"][1:],
                "families": result["families"][1:],
            },
            LimitationsQuery(show="cannot_serve"),
        ),
    }
