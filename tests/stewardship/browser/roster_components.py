"""Roster changes to enter (#528, step 4), served with its production template.

The list is rendered through the view's own sorting at its real address
(``PATH``) and at its second page (the navigator's own Next address, so the
in-place GET refresh lands on a served page). Each row's tick posts to the
request's roster address; the fixture server answers the first row's with
the real Post/Redirect/Get redirect back to the list (``POSTS``), which then
no longer shows that row (``TICKED``). The list on All is served at the Show
filter's own GET address, and the second row's tick there redirects to the
list still on All (``AFTER_ALL``), with that row now marked.
"""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.reports.ministry_roster_views import SHOW, SORTING
from parishkit.stewardship.web.tables import paginate

CAMPAIGN = UUID(int=528)
PATH = reverse("admin:ministry_roster", args=[CAMPAIGN])
TICKED = PATH + "?ticked=1"
# The list on All after a tick there: the view's redirect keeps show=all.
AFTER_ALL = PATH + "?show=all&ticked=1"
# The list after a tick someone else changed first.
STALE = PATH + "?size=25&changed=1"
ROWS = 30


def _rows():
    """Thirty resolved joins, newest first by resolution time."""
    when = datetime(2054, 10, 5, 12, tzinfo=UTC)
    return [
        {
            "id": str(UUID(int=10_000 + index)),
            "member_name": f"Member {index:02d}",
            "member_duid": str(100 + index),
            "family_duid": 1000 + index,
            "ministry_name": "Food pantry",
            "ministry_duid": 9,
            "action": "join",
            "state": "resolved",
            "outcome": "joined",
            "source_resolved": False,
            "resolved_at": when + timedelta(minutes=index),
            "roster_eligible": True,
            "roster_entered": False,
            "roster_sequence": 0,
            "roster_at": None,
            "roster_by": "",
            "request_key": UUID(int=20_000 + index),
        }
        for index in range(ROWS)
    ]


def _tick(index):
    """The roster tick address of row ``index``."""
    return reverse(
        "admin:ministry_followup_roster", args=[CAMPAIGN, UUID(int=10_000 + index)]
    )


def components(context, admin):
    """The list (Not yet entered and All), its second page, and the list after
    a tick, at the addresses the view and the in-place GET refreshes use."""

    def page(rows, paging, show="todo", changed=False):
        """Render the list from ``rows`` with ``paging``'s table choices."""
        table = paginate(rows, paging, carry=[("show", show)], sorting=SORTING)
        return table, (
            "text/html",
            render_to_string(
                "stewardship/ministry-roster.html",
                context
                | {
                    "admin_chrome": admin,
                    "metadata": {"name": "Sample campaign"},
                    "table": table,
                    "campaign_id": CAMPAIGN,
                    "show": show,
                    "show_choices": SHOW.items(),
                    "changed": changed,
                    "tick_fields": table.page_fields(table.number),
                    "mutable": True,
                    "can_tick": True,
                    "export_timezones": ["UTC", "America/Detroit"],
                },
            ),
        )

    rows = _rows()
    first, served = page(rows, {"size": "25"})
    _, second = page(rows, {"size": "25", "page": "2"})
    _, ticked = page(rows[:-1], {"size": "25"})
    _, everything = page(rows, {"size": "25"}, show="all")
    marked = [dict(rows[-2], roster_entered=True, roster_sequence=1), *rows]
    del marked[-2]
    _, after_all = page(marked, {"size": "25"}, show="all")
    _, stale = page(rows, {"size": "25"}, changed=True)
    # The Show filter's own GET: the select, then its kept size and sort.
    shown = urlencode([("show", "all"), *first.view_fields])
    return {
        PATH: served,
        f"{PATH}?{first.next_query}": second,
        TICKED: ticked,
        f"{PATH}?{shown}": everything,
        AFTER_ALL: after_all,
        STALE: stale,
    }


# The first row's tick on Not yet entered, and the second row's on All.
POSTS = {
    _tick(ROWS - 1): (303, TICKED, ""),
    _tick(ROWS - 2): (303, AFTER_ALL, ""),
    # The third row's tick lost a race.
    _tick(ROWS - 3): (303, STALE, ""),
}
