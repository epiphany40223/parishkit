"""Roster changes to enter (#528, step 4), served with its production template.

The list is rendered through the view's own sorting at its real address
(``PATH``) and at its second page (the navigator's own Next address, so the
in-place GET refresh lands on a served page). Each row's tick posts to the
request's roster address; the fixture server answers the first row's with
the real Post/Redirect/Get redirect back to the list (``POSTS``), which then
no longer shows that row (``TICKED``). The list on All is served at the
address its Show segment links to (``show_links``, #1535), and the second
row's tick there redirects to the list still on All (``AFTER_ALL``), with
that row now marked. The list with one Ministry applied is served at the
filter form's own GET (``MINISTRY``), and the list on All for that Ministry
at the address that page's Show segment links to. The bulk dialog's POST
(``BULK_POST``) is answered by each test with a route; the dialog then
redraws the list from ``BULKED``, without the two newest rows.
``TWICE`` lists one change twice, resolved near midnight UTC, named by the
view's own ``row_controls`` so each row's day is the browser's (#558).
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.reports.ministry_roster_views import (
    SORTING,
    row_controls,
    show_links,
)
from parishkit.stewardship.web.tables import paginate

PATH = reverse("admin:ministry_roster")
TICKED = PATH + "?ticked=1"
# The filter form's Apply with Ministry 9: the hidden Show, the Ministry,
# then the kept size and sort.
MINISTRY = (
    PATH
    + "?"
    + urlencode(
        [("show", "todo"), ("ministry", "9"), ("size", "25"), ("sort", "-resolved")]
    )
)
# The list on All after a tick there: the view's redirect keeps show=all.
AFTER_ALL = PATH + "?show=all&ticked=1"
# The list after a tick someone else changed first.
STALE = PATH + "?size=25&changed=1"
# The list the bulk dialog redraws from (its data-refresh-url) once the two
# newest changes are marked; the test answers the dialog's POST itself.
BULKED = PATH + "?bulked=1"
BULK_POST = reverse("admin:ministry_roster_bulk")
BULK_KEY = UUID(int=30_000)
ROWS = 30
# One change listed twice (#558): both resolved at 03:30 UTC, the evening
# before in the Americas and the same day in Asia.
TWICE = "/roster-twice"
# The same list after its first row's tick: only the second row is left.
TWICE_TICKED = TWICE + "?ticked=1"
TWICE_AT = (
    datetime(2054, 10, 5, 3, 30, tzinfo=UTC),
    datetime(2054, 10, 2, 3, 30, tzinfo=UTC),
)


def _rows():
    """Thirty resolved joins, newest first by resolution time."""
    when = datetime(2054, 10, 5, 12, tzinfo=UTC)
    return [
        {
            "id": str(UUID(int=10_000 + index)),
            "member_name": f"Member {index:02d}",
            "member_duid": 100 + index,
            "family_name": f"Family {index:02d}, Pat and Lee",
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
            "label": f"Member {index:02d} — Food pantry",
        }
        for index in range(ROWS)
    ]


def _tick(index):
    """The roster tick address of row ``index``."""
    return reverse("admin:ministry_followup_roster", args=[UUID(int=10_000 + index)])


def components(context, admin):
    """The list (Not yet entered and All), its second page, and the list after
    a tick, at the addresses the view and the in-place GET refreshes use."""

    def page(rows, paging, show="todo", changed=False, bulk_key=BULK_KEY, ministry=""):
        """Render the list from ``rows`` with ``paging``'s table choices."""
        carry = [("show", show)] + ([("ministry", ministry)] if ministry else [])
        table = paginate(rows, paging, carry=carry, sorting=SORTING)
        return table, (
            "text/html",
            render_to_string(
                "stewardship/ministry-roster.html",
                context
                | {
                    "admin_chrome": admin,
                    "metadata": {"name": "Sample campaign"},
                    "table": replace(table, action=PATH),
                    "show": show,
                    "show_links": show_links(table, PATH),
                    "ministry": ministry,
                    "ministries": [{"duid": 9, "name": "Food pantry"}],
                    "changed": changed,
                    "tick_fields": table.page_fields(table.number),
                    "refresh_url": BULKED,
                    "bulk_key": bulk_key,
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
    one, one_ministry = page(rows, {"size": "25"}, ministry="9")
    _, one_everything = page(rows, {"size": "25"}, show="all", ministry="9")
    marked = [dict(rows[-2], roster_entered=True, roster_sequence=1), *rows]
    del marked[-2]
    _, after_all = page(marked, {"size": "25"}, show="all")
    _, stale = page(rows, {"size": "25"}, changed=True)
    # A fresh key on the redrawn list, as the view draws one per page.
    _, bulked = page(rows[:-2], {"size": "25"}, bulk_key=UUID(int=30_001))
    # One change listed twice, named as the view names it.
    twice = [
        dict(row, resolved_at=when)
        for row, when in zip(rows[:2], TWICE_AT, strict=True)
    ]
    for row in twice:
        row["member_name"] = "Member 00"
    row_controls(twice)
    _, listed_twice = page(twice, {"size": "25"})
    _, twice_ticked = page(twice[1:], {"size": "25"})
    # The Show segment's own GET (show_links): All, the kept sort and size,
    # the first page.
    return {
        PATH: served,
        f"{PATH}?{first.next_query}": second,
        TICKED: ticked,
        show_links(first, PATH)["all"]: everything,
        MINISTRY: one_ministry,
        show_links(one, PATH)["all"]: one_everything,
        AFTER_ALL: after_all,
        STALE: stale,
        BULKED: bulked,
        TWICE: listed_twice,
        TWICE_TICKED: twice_ticked,
        # Nothing left to enter, and nothing for one chosen Ministry: the
        # two empty states name different next steps (#227).
        "/roster-none": page([], {"size": "25"})[1],
        "/roster-none-ministry": page([], {"size": "25"}, ministry="9")[1],
        "/roster-none-all": page([], {"size": "25"}, show="all")[1],
        "/roster-none-all-ministry": page([], {"size": "25"}, show="all", ministry="9")[
            1
        ],
    }


# The first row's tick on Not yet entered, and the second row's on All.
POSTS = {
    _tick(ROWS - 1): (303, TICKED, ""),
    _tick(ROWS - 2): (303, AFTER_ALL, ""),
    # The third row's tick lost a race.
    _tick(ROWS - 3): (303, STALE, ""),
    # The first row of the change listed twice (#558).
    _tick(0): (303, TWICE_TICKED, ""),
}
