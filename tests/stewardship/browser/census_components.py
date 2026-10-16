"""Census changes (#528): one shared POST table, as the view renders it.

The in-place paging and sorting test needs the real page under a few table
choices. Each fixture path renders it through the view's own sorting and the
real template, from 30 shaped rows (two pages of 25), so the headings' and
navigator's POST forms carry exactly what the production page would.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.reports.census_change_views import (
    KIND_LABELS,
    ROUTE_LABELS,
    SORTING,
    STATUS_LABELS,
    bulk_actions,
    row_controls,
)
from parishkit.stewardship.reports.census_changes import CensusQuery, select, shape
from parishkit.stewardship.responses.comparison import phone_record
from parishkit.stewardship.web.tables import paginate

CAMPAIGN = UUID(int=528)
# The page's own path: what every heading, navigator and filter form posts.
PATH = reverse("admin:census_changes")
ROWS = 30
# The notices the view answers a resolution of the first row with.
RESOLVED = (
    "Mobile phone for Member DUID 100 in Family DUID 1000 marked entered in ParishSoft."
)
# The page whose first row is Automatic (#528, ADM-09), at an address of its
# own that its forms post back to, and the notice the view answers an
# Administrator's Approve of that row with.
AUTOMATIC = "/census-changes-automatic"
APPROVED = "Mobile phone for Member DUID 100 in Family DUID 1000 approved."
# The page whose first three rows are Automatic and offer bulk review, and
# the notice the view answers an Administrator's bulk Approve of two with.
BULK = "/census-changes-bulk"
BULK_APPROVED = "2 changes approved."
# The notice the view answers Save value on the first Automatic row with, the
# value saved, and the page after it, where that row has the edit (ADM-09.04).
EDITED = "Mobile phone for Member DUID 100 in Family DUID 1000 now has an edited value."
EDITED_VALUE = "(317) 555-0199"
EDITED_PAGE = "/census-changes-edited"
STALE = (
    "Mobile phone for Member DUID 100 in Family DUID 1000 was updated by "
    "someone else, so nothing was recorded. The list shows its current status."
)


def components(context, admin):
    """The page at each table choice the test steps through."""
    when = datetime(2054, 10, 5, 12, tzinfo=UTC)

    def shaped(index, automatic, approved=0, edited=False):
        """One shaped row; the first ``automatic`` rows are Automatic, and the
        first ``approved`` of them approved (one version on); with ``edited``,
        the first has an Administrator's edited value (one version on)."""
        edit = edited and index == 0
        return shape(
            {
                "id": str(index),
                "entity_kind": "member",
                "entity_key": str(100 + index),
                "field": "mobile_phone",
                "baseline_available": True,
                "baseline_value": "555-0100",
                # Stored as the Family form stores a phone: a record.
                "submitted_value": phone_record(f"555-{index:04d}"),
                "current_available": True,
                "current_value": "555-0100",
                "admin_value_set": edit,
                "admin_value": phone_record(EDITED_VALUE) if edit else None,
                "handling": "api" if index < automatic else "manual",
                "decision": "approved" if index < approved else "unreviewed",
                "execution": "pending",
                # Later Families answered later, so newest first reverses.
                "submitted_at": when + timedelta(minutes=index),
                "family_duid": 1000 + index,
                "family_name": f"Family {index:02d}",
                "member_name": f"Member {index:02d}",
                "version": 2 if index < approved or edit else 1,
            }
        )

    query = CensusQuery()

    def page(
        paging, notice=None, *, automatic=False, bulk=False, approved=0, edited=False
    ):
        """Render the page with the table paged and sorted as ``paging`` says,
        and ``notice`` (a resolution's answer) above it; with ``automatic``,
        its first row is an Automatic change an Administrator reviews, and
        its forms post to ``AUTOMATIC``; with ``bulk``, its first three rows
        are, it offers bulk review, and its forms post to ``BULK``; with
        ``edited``, its first (Automatic) row has an edited value."""
        path = BULK if bulk else AUTOMATIC if automatic else PATH
        count = 3 if bulk else int(automatic)
        rows = [shaped(index, count, approved, edited) for index in range(ROWS)]
        result = select(rows, query, administrator=True)
        table = paginate(
            result["rows"],
            paging,
            carry=list(query.form_values().items()),
            sorting=SORTING,
        )
        row_controls(table.rows)
        return (
            "text/html",
            render_to_string(
                "stewardship/census-changes.html",
                context
                | {"admin_chrome": admin}
                | result
                | {
                    "table": replace(table, method="post", action=path),
                    "campaign_id": CAMPAIGN,
                    "query": query,
                    "query_fields": query.form_values(),
                    "status_choices": STATUS_LABELS.items(),
                    "route_choices": ROUTE_LABELS.items(),
                    "kind_choices": KIND_LABELS.items(),
                    "export_timezones": ["UTC", "America/Detroit"],
                    "notice": notice,
                    "action": path,
                    "mutable": True,
                    "bulk": bulk,
                    "bulk_actions": bulk_actions(),
                    # A fresh key per drawn page, as the view draws one.
                    "bulk_key": uuid4(),
                    "edit_today": "2054-10-06",
                },
            ),
        )

    return {
        "/census-changes": page({"size": "25"}),
        # The page at its own address, which its row forms post back to, so
        # an in-place answer from there is this same page.
        PATH: page({"size": "25"}),
        "/census-changes-page-2": page({"size": "25", "page": "2"}),
        "/census-changes-newest": page({"size": "25", "sort": "-submitted"}),
        "/census-changes-resolved": page(
            {"size": "25"},
            notice=RESOLVED,
        ),
        # The first row Automatic, To review; and the answer to its Approve.
        AUTOMATIC: page({"size": "25"}, automatic=True),
        "/census-changes-approved": page(
            {"size": "25"}, notice=APPROVED, automatic=True
        ),
        # The answer to Save value on the first (Automatic) row.
        EDITED_PAGE: page({"size": "25"}, notice=EDITED, automatic=True, edited=True),
        # Three Automatic rows with bulk review; and the answer to Approve.
        BULK: page({"size": "25"}, bulk=True),
        "/census-changes-bulk-approved": page(
            {"size": "25"}, notice=BULK_APPROVED, bulk=True, approved=2
        ),
        # A row someone else changed first: the page says so, nothing saved.
        "/census-changes-stale": page(
            {"size": "25"},
            notice=STALE,
        ),
    }
