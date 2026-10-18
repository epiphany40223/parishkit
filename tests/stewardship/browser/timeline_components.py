"""A Family's Timeline as its view renders it, from synthetic records (#477, #1570).

Each page is the real template with the Admin chrome, shaped by the view's
own ``page_context`` from the Timeline unit test records. Besides the
Administrator's page, the fixtures serve the pages its When heading and its
filter bar fetch at the exact addresses they carry (the filter bar's in the
sorted-query form the fixture server also matches), so the script's fetch is
an ordinary page request and the swapped-in region is the real template
output, plus Staff's reduced view and pages whose Open form is unavailable.
"""

from datetime import timedelta
from urllib.parse import urlencode
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.timeline_views import (
    TimelineQuery,
    page_context,
    timeline_url,
)
from parishkit.stewardship.reports.timelines import Timeline
from parishkit.stewardship.web.dates import using

from ..test_response_metrics import START
from ..test_timelines import CAMPAIGN, FAMILY, IDENTITY, TIMELINE
from .automation_components import canonical

ADMIN = timeline_url(FAMILY)
# The addresses the When heading leads to, as the shared table builds them:
# oldest first, then back to newest first.
OLDEST = ADMIN + "?size=all&sort=when"
NEWEST = ADMIN + "?size=all&sort=-when"
# The address the filter bar's Show lines sends for Kind Emails, from the
# fixture browser (Pacific): its zone, the table's hidden size and sort, and
# the blank days, as a native GET form sends them, in the order the fixture
# server's fallback sorts them to.
EMAILS = canonical(
    ADMIN
    + "?"
    + urlencode(
        [
            ("zone", "America/Los_Angeles"),
            ("size", "all"),
            ("sort", "-when"),
            ("kind", "emails"),
            ("start", ""),
            ("end", ""),
        ]
    )
)
STAFF = "/timeline-staff"
UNAVAILABLE = "/timeline-unavailable"
# A Family ParishSoft no longer lists as active and registered, while other
# campaign work holds exports: Open form and Queue timeline export are
# unavailable, each named by its reason (#1331).
LOCKED = "/timeline-locked"
# The export form's values, as the view's ``export_context`` gives them.
EXPORT = {
    "url": "/timeline-export",
    "request_key": UUID(int=99),
    "timezones": ["America/Los_Angeles", "America/New_York", "UTC"],
    "mutable": True,
}


def render(context, admin, timeline, **options):
    """One Timeline page for ``timeline``, shaped by the view's own context."""
    shaped = page_context(
        CAMPAIGN, FAMILY, IDENTITY, timeline, START + timedelta(days=1), **options
    )
    return render_to_string(
        "stewardship/timeline.html", context | {"admin_chrome": admin} | shaped
    )


def components(context, admin):
    """The Administrator's page, its sorts and filter, Staff's and unavailable ones."""
    pages = {
        ADMIN: (TIMELINE, {"full": True}),
        OLDEST: (TIMELINE, {"full": True, "values": {"size": "all", "sort": "when"}}),
        NEWEST: (TIMELINE, {"full": True, "values": {"size": "all", "sort": "-when"}}),
        EMAILS: (
            TIMELINE,
            {
                "full": True,
                "query": TimelineQuery("emails"),
                "values": {"size": "all", "sort": "-when"},
            },
        ),
        STAFF: (Timeline(TIMELINE.summary), {}),
        UNAVAILABLE: (
            TIMELINE,
            {"full": True, "testing_codes": True, "family_test_url": ADMIN},
        ),
        LOCKED: (
            TIMELINE,
            {"full": True, "eligible": False, "export": EXPORT | {"mutable": False}},
        ),
    }
    responses = {}
    with using("us_long"):
        for path, (timeline, options) in pages.items():
            # Administrators and Staff both may see Family codes.
            options = {"show_codes": True, "export": EXPORT} | options
            responses[path] = ("text/html", render(context, admin, timeline, **options))
    return responses
