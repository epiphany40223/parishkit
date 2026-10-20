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
from parishkit.stewardship.responses.refresh_checks import CheckState
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
# Row 3 Entered incorrectly (#1737): a refresh found ParishSoft at a third
# value, and the pages after each of its two fixes.
INCORRECT = "/census-changes-incorrect"
WRONG = "555-0199"
REENTERED = "/census-changes-reentered"
REENTERED_NOTICE = (
    "Mobile phone for Member DUID 103 in Family DUID 1003 marked entered by hand again."
)
RETURNED = "/census-changes-returned"
RETURNED_NOTICE = (
    "Mobile phone for Member DUID 103 in Family DUID 1003 sent back to review."
)
# Row 4 a New Member (#1749), whose Entered by hand asks for the ParishSoft
# Member ID; row 5 an Updated in ParishSoft change a refresh put back in
# Conflict; and the page after the New Member's mark.
NEW_MEMBER = "/census-changes-new-member"
MEMBER_ENTERED = "/census-changes-member-entered"
MEMBER_ENTERED_NOTICE = (
    "New Member for a New Member in Family DUID 1004 marked entered by hand."
)
# The page after the New Member's Member ID is corrected to 9 (#1773).
MEMBER_CORRECTED = "/census-changes-member-corrected"
MEMBER_CORRECTED_NOTICE = (
    "New Member for a New Member in Family DUID 1004 ParishSoft Member ID corrected."
)


def components(context, admin):
    """The page at each table choice and after each decision the tests take."""
    when = datetime(2054, 10, 5, 12, tzinfo=UTC)

    def shaped(index, accepted=(), entered=(), incorrect=(), added=False, member=7):
        """One shaped row: To review unless ``accepted`` (approved, one
        version on), ``entered`` (Entered by hand) or ``incorrect`` (Entered
        by hand, then found at a third value) holds its index. With
        ``added``, row 4 is a New Member (marked with Member ID ``member``)
        and row 5 a refresh Conflict."""
        family = index == 2
        entered = set(entered) | set(incorrect)
        if added and index == 4:
            return shape(new_member(index, entered=index in entered, member=member))
        if added and index == 5:
            return shape(
                row(index)
                | {
                    "decision": "unreviewed",
                    "execution": "conflict",
                    "handling": "api",
                    "current_value": "555-0100",
                    "refresh_conflict": True,
                }
            )
        return shape(
            row(
                index,
                family=family,
                accepted=accepted,
                entered=entered,
                incorrect=incorrect,
            )
        )

    def new_member(index, *, entered, member):
        """Row ``index`` as a New Member the Family added (#1749), marked
        with Member ID ``member`` when ``entered``; a correction (#1773)
        is one version on."""
        return row(index) | {
            "entity_kind": "proposed_member",
            "entity_key": "local-key",
            "field": "new_member",
            "baseline_available": False,
            "baseline_value": None,
            "submitted_value": {"first_name": "Newly", "last_name": "Sample"},
            "current_available": False,
            "current_value": None,
            "handling": "manual",
            "decision": "approved" if entered else "unreviewed",
            "execution": "resolved_external" if entered else "pending",
            "version": (2 if member == 7 else 3) if entered else 1,
            "resolution_action": "entered" if entered else None,
            "resolution_member": member if entered else None,
            "member_status": None,
        }

    def row(index, *, family=False, accepted=(), entered=(), incorrect=()):
        """Row ``index``'s query columns, as ``shaped`` describes them."""
        return {
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
            # Member 01 moved to another Family (#1738): the change keeps
            # its stage and the Who cell shows the shared badge.
            "member_status": "moved" if index == 1 else None,
            "check": CheckState(
                "entered_incorrectly", {"available": True, "value": WRONG}
            )
            if index in incorrect
            else None,
        }

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
        NEW_MEMBER: page(size, added=True),
        MEMBER_ENTERED: page(size, MEMBER_ENTERED_NOTICE, added=True, entered={4}),
        MEMBER_CORRECTED: page(
            size, MEMBER_CORRECTED_NOTICE, added=True, entered={4}, member=9
        ),
        INCORRECT: page(size, incorrect={3}),
        REENTERED: page(size, REENTERED_NOTICE, entered={3}),
        RETURNED: page(size, RETURNED_NOTICE),
        # No changes at all, and none at the stage chosen.
        "/census-changes-none": page(size, rows=0),
        "/census-changes-none-stage": page(size, rows=0, stage="to_review"),
    }
