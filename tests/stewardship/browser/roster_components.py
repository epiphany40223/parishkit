"""Roster changes to enter (#528 step 4, #1687), served with its production
template and the view's own page context (``page_context``).

The list is the Ministry stream's manual handling report on the shared
review worklist: each page below is the list for one view, which the tests
answer the page's own POSTs with (``fulfil_post_with``), as the view answers
with the page redrawn. ``PATH`` is the default view (To enter by hand);
``MARKED_ONE`` the same view after the newest change's Entered by hand,
``BULKED`` after the two newest were marked together, ``STALE`` after a
mark someone else made first, ``ENTERED`` the Entered by hand Stage and
``HELD`` the list while other campaign work holds it.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.pipeline import Decision, Placement, Stage
from parishkit.stewardship.reports.ministry_roster_views import page_context

PATH = reverse("admin:ministry_roster")
MARKED_ONE = "/roster-marked-one"
BULKED = "/roster-bulked"
STALE = "/roster-stale"
ENTERED = "/roster-entered"
HELD = "/roster-held"
EMPTY = "/roster-none"
ROWS = 30
MARKED_NOTICE = "Member 29 — Food pantry marked entered by hand."
BULK_NOTICE = "2 changes marked entered by hand."
STALE_NOTICE = "Member 27 — Food pantry: not recorded, changed by someone else."
WHEN = datetime(2054, 10, 5, 12, tzinfo=UTC)


def request_id(index):
    """Row ``index``'s request id (29 is the newest)."""
    return str(UUID(int=10_000 + index))


def _row(index, entered=False):
    """One accepted join of the fixture list, as ``roster_rows`` and
    ``with_decisions`` shape it."""
    stage = Stage.ENTERED_BY_HAND if entered else Stage.TO_ENTER_BY_HAND
    return {
        "id": request_id(index),
        "member_name": f"Member {index:02d}",
        "member_label": f"Member {index:02d}",
        "member_duid": 100 + index,
        "member_status": "current",
        "source_mark": None,
        "family_name": f"Family {index:02d}, Pat and Lee",
        "family_id": None,
        "family_duid": 1000 + index,
        "ministry_name": "Food pantry",
        "family_ministry_name": "Food pantry",
        "ministry_duid": 9,
        "action": "join",
        "state": "resolved",
        "outcome": "joined",
        "source_resolved": False,
        "resolved_at": WHEN + timedelta(minutes=index),
        "submitted_at": WHEN - timedelta(days=1, minutes=index),
        "decided_by": "leader@example.org",
        "decided_at": WHEN + timedelta(minutes=index),
        "note": "",
        "latest": True,
        "roster_entered": entered,
        "roster_sequence": 1 if entered else 0,
        "roster_at": WHEN + timedelta(hours=2) if entered else None,
        "roster_by": "staff@example.org" if entered else "",
        "placement": Placement(stage, Decision.ACCEPTED),
        "stage": stage.value,
    }


def components(context, admin):
    """The list for each view the tests open or are answered with."""

    def page(
        rows, stage="to_enter_by_hand", notice=None, roles=("staff",), ministry=""
    ):
        """Render the list from ``rows`` for one view."""
        result = {
            "metadata": {"name": "Sample campaign"},
            "ministries": [{"duid": 9, "name": "Food pantry"}],
        }
        values = page_context(
            rows,
            result,
            stage,
            {"ministry": ministry, "paging": {"size": "25"}},
            frozenset(roles),
            mutable=bool(roles),
            notice=notice,
            prepare=lambda shown: None,
        )
        return (
            "text/html",
            render_to_string(
                "stewardship/ministry-roster.html",
                context | {"admin_chrome": admin} | values,
            ),
        )

    rows = [_row(index) for index in range(ROWS)]
    one = [*rows[:-1], _row(ROWS - 1, entered=True)]
    two = [*rows[:-2], _row(ROWS - 2, entered=True), _row(ROWS - 1, entered=True)]
    # Someone else marked Member 27 first: the list shows it entered.
    raced = [_row(index, entered=index == ROWS - 3) for index in range(ROWS)]
    return {
        PATH: page(rows),
        MARKED_ONE: page(one, notice=MARKED_NOTICE),
        BULKED: page(two, notice=BULK_NOTICE),
        STALE: page(raced, notice=STALE_NOTICE),
        ENTERED: page(one, stage="entered_by_hand"),
        HELD: page(rows, roles=()),
        EMPTY: page([]),
        # Nothing at a Stage, and nothing for one chosen Ministry: each empty
        # state names its own next step (#227).
        "/roster-none": page([]),
        "/roster-none-ministry": page([], ministry="9"),
        "/roster-none-all": page([], stage="all"),
        "/roster-none-all-ministry": page([], stage="all", ministry="9"),
    }
