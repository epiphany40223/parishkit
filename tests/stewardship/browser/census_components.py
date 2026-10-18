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
    row_dialogs,
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
# The page whose first (Automatic) row is a Conflict, the notice the view
# answers its Use the Family's value anyway with, and the page after it,
# where that row is approved and pending (ADM-09.03).
CONFLICT = "/census-changes-conflict"
CONFLICT_RESOLVED = (
    "Mobile phone for Member DUID 100 in Family DUID 1000 approved over "
    "ParishSoft's current value."
)
CONFLICT_PAGE = "/census-changes-conflict-resolved"
# The Automatic page redrawn with other view settings, nothing saved: 50 rows
# a page; then also sorted newest first; then also filtered to Automatic
# changes. Its first row's Edit value is drawn at the same version each time
# (#1334 re-review).
AUTOMATIC_REDRAWS = (
    "/census-changes-automatic-50",
    "/census-changes-automatic-newest",
    "/census-changes-automatic-filtered",
)
STALE = (
    "Mobile phone for Member DUID 100 in Family DUID 1000 was updated by "
    "someone else, so nothing was recorded. The list shows its current status."
)

# The page whose rows carry the longest values staff meet (#1472): a long
# Family name, a Family address, a New Member, an edited Conflict and a
# recorded change with its sign-in address and note.
WIDE = "/census-changes-wide"


def wide_rows(when):
    """Shaped rows with realistic long values, one of each kind of row."""
    family = "Fernández-Castellanos, Maria Guadalupe and Christopher"
    address = {
        "line1": "12345 North Saint Bartholomew Boulevard",
        "line2": "Apartment 1204",
        "city": "Louisville",
        "region": "KY",
        "postal_code": "40241-1234",
    }
    base = {
        "entity_kind": "member",
        "field": "mobile_phone",
        "baseline_available": True,
        "baseline_value": "555-0100",
        "submitted_value": phone_record("(502) 555-0142"),
        "current_available": True,
        "current_value": "555-0100",
        "admin_value_set": False,
        "admin_value": None,
        "handling": "manual",
        "decision": "unreviewed",
        "execution": "pending",
        "submitted_at": when,
        "family_duid": 1234567,
        "family_name": family,
        "member_name": "Fernández-Castellanos, Maria Guadalupe",
        "version": 1,
    }
    changes = [
        {
            "entity_kind": "family",
            "entity_key": "1234567",
            "field": "home_address",
            "current_available": False,
            "submitted_value": address,
        },
        {
            "entity_key": "7654321",
            "handling": "api",
            "execution": "conflict",
            "current_value": "(502) 555-0177",
            "admin_value_set": True,
            "admin_value": phone_record("(502) 555-0199"),
        },
        {
            "entity_kind": "proposed_member",
            "entity_key": "new-1",
            "field": "new_member",
            "current_available": False,
            "baseline_available": False,
            "submitted_value": {
                "first_name": "Bartholomew",
                "last_name": "Fernández-Castellanos",
                "email": "bartholomew.fernandez-castellanos@example.org",
            },
        },
        {
            "entity_key": "7654322",
            "field": "email",
            "execution": "resolved_external",
            "current_value": "maria.guadalupe.fernandez@example.org",
            "submitted_value": "maria.fernandez-castellanos@example.org",
            "resolution_action": "entered",
            "resolution_by": "parish.office.administrator@example.org",
            "resolution_at": when + timedelta(days=1),
            "resolution_note": "Typed into ParishSoft after calling the Family "
            "to confirm the new address and spelling.",
        },
    ]
    return [
        shape(
            base
            | {"id": f"w{index}", "submitted_at": when + timedelta(minutes=index)}
            | change
        )
        for index, change in enumerate(changes)
    ]


def components(context, admin):
    """The page at each table choice the test steps through."""
    when = datetime(2054, 10, 5, 12, tzinfo=UTC)

    def shaped(index, automatic, approved=0, edited=False, conflict=False):
        """One shaped row; the first ``automatic`` rows are Automatic, and the
        first ``approved`` of them approved (one version on); with ``edited``,
        the first has an Administrator's edited value (one version on); with
        ``conflict``, the first is a Conflict (ParishSoft changed since)."""
        edit = edited and index == 0
        clash = conflict and index == 0
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
                "current_value": "555-0177" if clash else "555-0100",
                "admin_value_set": edit,
                "admin_value": phone_record(EDITED_VALUE) if edit else None,
                "handling": "api" if index < automatic else "manual",
                "decision": "approved" if index < approved else "unreviewed",
                "execution": "conflict" if clash else "pending",
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
        paging,
        notice=None,
        *,
        automatic=False,
        bulk=False,
        approved=0,
        edited=False,
        conflict=False,
        path=None,
        filters=None,
        rows=ROWS,
        wide=False,
    ):
        """Render the page with the table paged and sorted as ``paging`` says,
        and ``notice`` (a resolution's answer) above it; with ``automatic``,
        its first row is an Automatic change an Administrator reviews, and
        its forms post to ``AUTOMATIC``; with ``bulk``, its first three rows
        are, it offers bulk review, and its forms post to ``BULK``; with
        ``edited``, its first (Automatic) row has an edited value; with
        ``conflict``, it is a Conflict; ``path`` is where its forms post;
        ``filters`` (a CensusQuery) replace the default filters; ``rows``
        is how many changes there are (none draws the empty table); with
        ``wide``, the rows are WIDE_ROWS' realistic long values instead."""
        path = path or (BULK if bulk else AUTOMATIC if automatic else PATH)
        shown = filters or query
        count = 3 if bulk else int(automatic)
        rows = [
            shaped(index, count, approved, edited, conflict) for index in range(rows)
        ]
        if wide:
            rows = wide_rows(when)
        result = select(rows, shown, administrator=True)
        table = paginate(
            result["rows"],
            paging,
            carry=list(shown.form_values().items()),
            sorting=SORTING,
        )
        row_controls(table.rows, administrator=True, mutable=True)
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
                    "query": shown,
                    "query_fields": shown.form_values(),
                    "status_choices": STATUS_LABELS.items(),
                    "route_choices": ROUTE_LABELS.items(),
                    "kind_choices": KIND_LABELS.items(),
                    "export_timezones": ["UTC", "America/Detroit"],
                    "notice": notice,
                    "action": path,
                    "mutable": True,
                    "bulk": bulk,
                    "bulk_actions": bulk_actions(),
                    "row_dialogs": row_dialogs(True),
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
        # The first row an Automatic Conflict; and the answer to its Use the
        # Family's value anyway, where it is approved and pending.
        CONFLICT: page({"size": "25"}, automatic=True, conflict=True, path=CONFLICT),
        CONFLICT_PAGE: page(
            {"size": "25"},
            notice=CONFLICT_RESOLVED,
            automatic=True,
            approved=1,
            path=CONFLICT,
        ),
        # Three Automatic rows with bulk review; and the answer to Approve.
        BULK: page({"size": "25"}, bulk=True),
        AUTOMATIC_REDRAWS[0]: page({"size": "50"}, automatic=True),
        AUTOMATIC_REDRAWS[1]: page(
            {"size": "50", "sort": "-submitted"}, automatic=True
        ),
        AUTOMATIC_REDRAWS[2]: page(
            {"size": "50", "sort": "-submitted"},
            automatic=True,
            filters=CensusQuery(route="automatic"),
        ),
        "/census-changes-bulk-approved": page(
            {"size": "25"}, notice=BULK_APPROVED, bulk=True, approved=2
        ),
        # A row someone else changed first: the page says so, nothing saved.
        "/census-changes-stale": page(
            {"size": "25"},
            notice=STALE,
        ),
        # No changes at all: with no filter, with only Every status, with
        # Every status and history, and with a search (#227: each says why
        # the table is empty).
        "/census-changes-none": page({"size": "25"}, rows=0),
        "/census-changes-none-all": page(
            {"size": "25"}, rows=0, filters=CensusQuery(status="all")
        ),
        "/census-changes-none-everything": page(
            {"size": "25"},
            rows=0,
            filters=CensusQuery(status="all", history="yes"),
        ),
        # Realistic long values at their widest (#1472): the column-fit test.
        WIDE: page({"size": "25"}, wide=True, filters=CensusQuery(status="all")),
        "/census-changes-none-searched": page(
            {"size": "25"}, rows=0, filters=CensusQuery(search="Nobody")
        ),
    }
