"""The talents report: two shared tables on one page, as the view renders them.

The in-place re-sort tests (#478) need a page whose tables must keep each
other's choices. Each fixture path renders the report under one combination
of the two tables' sorts, built through the view's own ``tables`` helper, so
the headings' POST forms carry exactly what the production page would.
"""

from datetime import UTC, datetime
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.reports.talent_views import tables
from parishkit.stewardship.reports.talents import TalentQuery

CAMPAIGN = UUID(int=90)
# The report page's own path: what every heading, navigator and filter form
# posts back to, and what the tests route.
PATH = reverse("admin:talents_report")


def components(context, admin):
    """The report under each sort combination the tests step through."""
    when = datetime(2026, 9, 20, 12, tzinfo=UTC)
    result = {
        "metadata": {"name": "Sample campaign"},
        "summary": {
            "members": 2,
            "cannot_serve": 1,
            "cannot_attend": 2,
            "talents": [("Music", 1)],
        },
        "collects_talents": True,
        "talent_choices": [("music", "Music")],
        # Default order is by Family: Baker's member before Carter's. By
        # Member name the order reverses.
        "members": [
            {
                "member_name": "Zed Able",
                "family_name": "Baker",
                "family_duid": 2,
                "proposed": False,
                "talents": ["Music"],
                "cannot_serve": False,
                "submitted_at": when,
            },
            {
                "member_name": "Amy Young",
                "family_name": "Carter",
                "family_duid": 1,
                "proposed": True,
                "talents": [],
                "cannot_serve": True,
                "submitted_at": when,
            },
        ],
        "families": [
            {"family_name": "Baker", "family_duid": 2, "submitted_at": when},
            {"family_name": "Carter", "family_duid": 1, "submitted_at": when},
        ],
    }
    query = TalentQuery()

    def page(paging, data=result, chosen=query):
        """Render the report with both tables sorted as ``paging`` says,
        from ``data`` under the filters ``chosen``."""
        members, families = tables(data, chosen, paging, PATH)
        return (
            "text/html",
            render_to_string(
                "stewardship/talents-report.html",
                context
                | {"admin_chrome": admin}
                | data
                | {
                    "members_table": members,
                    "families_table": families,
                    "campaign_id": CAMPAIGN,
                    "query": chosen,
                    "query_fields": chosen.form_values(),
                    "export_timezones": ["UTC", "America/Detroit"],
                },
            ),
        )

    return {
        "/talents-report": page({}),
        "/talents-report-families-desc": page({"families_sort": "-family"}),
        "/talents-report-both": page(
            {"families_sort": "-family", "members_sort": "member"}
        ),
        # What Show "Cannot participate in ministries" returns (#484): one
        # Member, one Family and a smaller summary.
        "/talents-report-cannot-serve": page(
            {},
            result
            | {
                "summary": result["summary"] | {"members": 1, "cannot_attend": 1},
                "members": result["members"][1:],
                "families": result["families"][1:],
            },
            TalentQuery(talent="cannot_serve"),
        ),
    }
