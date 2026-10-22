"""A user's Timeline as its view renders it, from synthetic records (#1866).

Each page is the real template with the Admin chrome, shaped by the view's
own ``page_context`` from the unit test's records: a Staff user with one
role change, sixty sign-ins and one email, so the table has two pages.
Besides the first page (``PAGE``), the fixtures serve the pages its
controls fetch at the exact addresses they carry: the navigator's Next
(``NEXT``), the When heading's oldest first (``OLDEST``) and the filter
bar's Kind Role changes (``ROLES``) and Pages viewed (``VIEWS``, #2006),
each in the sorted-query form the fixture server also matches.
"""

from urllib.parse import urlencode

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.user_timeline_views import (
    PAGE_SIZE,
    UserTimelineQuery,
    page_context,
)
from parishkit.stewardship.web.dates import using

from ..test_response_metrics import START
from ..test_user_timelines import USER, timeline
from .automation_components import canonical

PAGE = reverse("admin:user_timeline", args=[USER])
RECORDS = timeline(PAGE_SIZE + 10)
AS_OF = START.replace(hour=23)


def _table(values):
    """The shared table the view builds for these table values."""
    return page_context(RECORDS, UserTimelineQuery(), values, AS_OF)["table"]


# The navigator's Next and the When heading's oldest first, as the shared
# table builds their queries.
NEXT = PAGE + "?" + _table({}).next_query
OLDEST = PAGE + "?size=50&sort=when"


def applied(kind):
    """The filter bar's Apply filters address for ``kind``.

    From the Pacific fixture browser: its zone, the table's hidden size
    and sort, and the blank days, as a native GET form sends them.
    """
    return canonical(
        PAGE
        + "?"
        + urlencode(
            [
                ("zone", "America/Los_Angeles"),
                ("size", "50"),
                ("sort", "-when"),
                ("kind", kind),
                ("start", ""),
                ("end", ""),
            ]
        )
    )


ROLES = applied("roles")
VIEWS = applied("views")


def components(context, admin):
    """The first page, the next one, the oldest-first one and two Kinds."""
    pages = {
        PAGE: (UserTimelineQuery(), {}),
        NEXT: (UserTimelineQuery(), {"page": "2", "size": "50", "sort": "-when"}),
        OLDEST: (UserTimelineQuery(), {"size": "50", "sort": "when"}),
        ROLES: (UserTimelineQuery("roles"), {"size": "50", "sort": "-when"}),
        VIEWS: (UserTimelineQuery("views"), {"size": "50", "sort": "-when"}),
    }
    responses = {}
    with using("us_long"):
        for path, (query, values) in pages.items():
            shaped = page_context(RECORDS, query, values, AS_OF)
            responses[path] = (
                "text/html",
                render_to_string(
                    "stewardship/user-timeline.html",
                    context | {"admin_chrome": admin} | shaped,
                ),
            )
    return responses
