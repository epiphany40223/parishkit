"""Census changes (#528, #1683): the review worklist, as the view renders it.

Each fixture path renders the real page through the view's own sorting,
``census_worklist.worklist_page`` and the real templates, from shaped rows,
so the headings', navigator's and dialogs' POST forms carry exactly what the
production page would. Each test answers a POST with the fixture page after
it, as the view answers with its page redrawn.
"""

from datetime import UTC, datetime, timedelta

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.reports.census_change_views import (
    KIND_LABELS,
    ROUTE_LABELS,
    SORTING,
)
from parishkit.stewardship.reports.census_changes import CensusQuery, select, shape
from parishkit.stewardship.reports.census_worklist import worklist_page
from parishkit.stewardship.responses.comparison import phone_record
from parishkit.stewardship.web.tables import paginate

# The page's own path: what every heading, navigator, filter and dialog posts.
PATH = reverse("admin:census_changes")
ROWS = 30
# Row 0 is Automatic and row 1 By hand, both To review; row 2 a Family
# address (By hand), every later row By hand too.
ADDRESS = {
    "line1": "9 Census Way",
    "line2": "",
    "city": "Louisville",
    "region": "KY",
    "postal_code": "40223",
    "country": "US",
}
# The answers the tests receive after a decision, and their notices.
ACCEPTED = "/census-changes-accepted"
ACCEPTED_NOTICE = "Mobile phone for Member DUID 100 in Family DUID 1000 accepted."
ENTERED = "/census-changes-entered"
ENTERED_NOTICE = (
    "Mobile phone for Member DUID 101 in Family DUID 1001 marked entered by hand."
)
BULKED = "/census-changes-bulked"
BULK_NOTICE = "2 changes accepted."
STALE = "/census-changes-stale"
STALE_NOTICE = (
    "Mobile phone for Member DUID 100 in Family DUID 1000 was changed by "
    "someone else first, so nothing was recorded. The list shows its current "
    "state."
)
EDITED = "/census-changes-edited"
EDITED_NOTICE = "Home address for the Family in Family DUID 1002 accepted (edited)."

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
    """The page at each table choice and after each decision the tests take."""
    when = datetime(2054, 10, 5, 12, tzinfo=UTC)

    def shaped(index, accepted=(), entered=()):
        """One shaped row: To review unless ``accepted`` (approved, one
        version on) or ``entered`` (Entered by hand) holds its index."""
        family = index == 2
        return shape(
            {
                "id": str(index),
                "entity_kind": "family" if family else "member",
                "entity_key": "1002" if family else str(100 + index),
                "field": "home_address" if family else "mobile_phone",
                "baseline_available": not family,
                "baseline_value": None if family else "555-0100",
                # Stored as the Family form stores a phone: a record.
                "submitted_value": (
                    ADDRESS if family else phone_record(f"555-{index:04d}")
                ),
                "current_available": not family,
                "current_value": None if family else "555-0100",
                "admin_value_set": False,
                "admin_value": None,
                "handling": "api" if index == 0 else "manual",
                "decision": "approved" if index in accepted else "unreviewed",
                "execution": "resolved_external" if index in entered else "pending",
                # Later Families answered later, so newest first reverses.
                "submitted_at": when + timedelta(minutes=index),
                "family_duid": 1000 + index,
                "family_name": f"Family {index:02d}",
                "member_name": f"Member {index:02d}",
                "version": 2 if index in accepted or index in entered else 1,
                "resolution_action": None,
                "resolution_note": None,
                "resolution_at": None,
                "resolution_by": None,
                "reopened": False,
            }
        )

    def page(paging, notice=None, *, stage="all", rows=ROWS, **changes):
        """Render the page with the table paged and sorted as ``paging`` says,
        the Stage filter at ``stage``, ``notice`` above the table, and
        ``changes`` (``shaped``'s) applied, for a Staff reader."""
        query = CensusQuery(stage=stage)
        found = [shaped(index, **changes) for index in range(rows)]
        result = select(found, query, administrator=False)
        result["roles"] = frozenset({"staff"})
        table = paginate(
            result["rows"],
            paging,
            carry=list(query.form_values().items()),
            sorting=SORTING,
        )
        worklist, table = worklist_page(result, query, table, PATH, mutable=True)
        return (
            "text/html",
            render_to_string(
                "stewardship/census-changes.html",
                context
                | {"admin_chrome": admin}
                | {
                    "worklist": worklist,
                    "table": table,
                    "query": query,
                    "query_fields": query.form_values(),
                    "route_choices": ROUTE_LABELS.items(),
                    "kind_choices": KIND_LABELS.items(),
                    "export_timezones": ["UTC", "America/Detroit"],
                    "notice": notice,
                    "mutable": True,
                    "administrator": False,
                    "edit_today": "2054-10-06",
                },
            ),
        )

    size = {"size": "25"}
    return {
        "/census-changes": page(size),
        PATH: page(size),
        "/census-changes-page-2": page({"size": "25", "page": "2"}),
        "/census-changes-who": page({"size": "25", "sort": "-who"}),
        ACCEPTED: page(size, ACCEPTED_NOTICE, accepted={0}),
        ENTERED: page(size, ENTERED_NOTICE, entered={1}),
        BULKED: page(size, BULK_NOTICE, accepted={0, 1}),
        STALE: page(size, STALE_NOTICE),
        EDITED: page(size, EDITED_NOTICE, accepted={2}),
        # No changes at all, and none at the stage chosen.
        "/census-changes-none": page(size, rows=0),
        "/census-changes-none-stage": page(size, rows=0, stage="to_review"),
    }
