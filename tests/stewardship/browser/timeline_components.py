"""A Family's Timeline as its view renders it, from synthetic records (#477, #1570).

Each page is the real template with the Admin chrome, shaped by the view's
own ``page_context`` from the Timeline unit test records. Besides the
Administrator's page, the fixtures serve the pages its When heading and its
filter bar fetch at the exact addresses they carry (the filter bar's in the
sorted-query form the fixture server also matches), so the script's fetch is
an ordinary page request and the swapped-in region is the real template
output, plus Staff's view (the full timeline with review lines and the
export, but no Mail message links, #1774, #1799) and pages whose Open form
is unavailable.
Every page is the Family hub (#1678) of a campaign with census, Financial and
Ministry stewardship: the header strip, then the Timeline tab, the Family tab
and one tab per Member (slice 2, from the census unit test's response, each
with its Ministry joins and leaves from slice 4), the Financial tab (slice
3) and the Additional tab (slice 4), all
from the ``hub_streams`` unit test's values. The fixtures also serve the hub
at each content tab's own address, which a tab's first choice fetches.
"""

from datetime import timedelta
from urllib.parse import urlencode
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.hub_census import shape
from parishkit.stewardship.reports.hub_streams import (
    financial_tab,
    ministry_rows,
    other_tab,
)
from parishkit.stewardship.reports.timeline_views import (
    TimelineQuery,
    page_context,
    timeline_url,
)
from parishkit.stewardship.reports.timelines import Timeline, events
from parishkit.stewardship.web.dates import using

from ..test_hub_actions import HOME_ID, census_worklist, family_tab, pledge_tab
from ..test_hub_census import ANSWERS, HOME, NAMES, NEW_HOME, ROWS
from ..test_hub_streams import (
    ITEM,
    METADATA,
    NAMES_BY_DUID,
    REQUESTS,
    other,
    shaped_pledge,
)
from ..test_response_metrics import START
from ..test_timelines import (
    CAMPAIGN,
    ENGAGEMENT,
    FAMILY,
    FIRST,
    IDENTITY,
    INVITATION,
    RECEIPT,
    REMINDER,
    SECOND,
    TIMELINE,
)
from .automation_components import canonical

ADMIN = timeline_url(FAMILY)
# The addresses the When heading leads to, as the shared table builds them:
# oldest first, then back to newest first.
OLDEST = ADMIN + "?size=all&sort=when"
NEWEST = ADMIN + "?size=all&sort=-when"
# The address the filter bar's Apply filters sends for Kind Emails, from the
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
# The address Apply filters sends for From 2026-10-04 (the day after every
# fixture line, in the Pacific fixture browser) with Through blank.
LATER = canonical(
    ADMIN
    + "?"
    + urlencode(
        [
            ("zone", "America/Los_Angeles"),
            ("size", "all"),
            ("sort", "-when"),
            ("kind", "all"),
            ("start", "2026-10-04"),
            ("end", ""),
        ]
    )
)
STAFF = "/timeline-staff"
# Staff's page (#1774): the fixture's lines plus two review lines, one at
# the second submission's instant (built after it) and one later.
REVIEWED = Timeline(
    TIMELINE.summary,
    events(
        [INVITATION, REMINDER, RECEIPT],
        [FIRST, SECOND],
        engagement=ENGAGEMENT,
        reviews=[
            (
                SECOND.at,
                "Accepted",
                "Email address for Adams, Ann; by staff@example.org",
            ),
            (
                SECOND.at + timedelta(hours=2),
                "Handled",
                "Additional information; by staff@example.org",
            ),
        ],
    ),
    ENGAGEMENT,
)
# The Administrator's page a minute later, with Staff's two review lines
# added: what the table's periodic re-read (#2197) brings in.
LIVE = "/timeline-live"
UNAVAILABLE = "/timeline-unavailable"
# A Family ParishSoft no longer lists as active and registered, while other
# campaign work holds exports: Open form and the export buttons are
# unavailable, each named by its reason (#1331).
LOCKED = "/timeline-locked"
# The hub's content sections for a campaign with every module (#1678), and
# its census tabs, from the census unit test's response.
HUB = ("family", "members", "financial", "other")
CENSUS = shape(ANSWERS, NAMES, ROWS)
# The census tabs' own addresses, as each tab's link carries them.
CENSUS_TABS = ("family", "member-1", "member-2", "member-7")
TAB = {key: ADMIN + "?tab=" + key for key in (*CENSUS_TABS, "financial", "other")}
# Each content tab's own stream, as ``hub_reads`` gives it for that tab:
# Member 1 has Ministry requests, the other Member tabs none.
STREAMS = {
    "member-1": {"hub_ministry": ministry_rows(REQUESTS, NAMES_BY_DUID)},
    "member-2": {"hub_ministry": ()},
    "member-7": {"hub_ministry": ()},
    "financial": {"hub_financial": financial_tab(shaped_pledge(), METADATA)},
    "other": {
        "hub_other": other_tab(
            other(
                cannot_attend=True,
                item=ITEM,
                text="Please call about the choir",
                disposition="current_actionable",
                follow_up_needed=True,
                followed_up=False,
            ),
            CENSUS.members,
            ministry=True,
            information=True,
        )
    },
}
# The Family tab with its row actions (#2064): its home address change as
# Census changes decorates it, To review, then once accepted (the page the
# tab redraws from after Accept; a test serves it at the tab's address).
HUB_ACTIONS = "/hub-actions"
HUB_ACCEPTED = "/hub-actions-accepted"
# The Financial tab with its pledge's row actions, the pledge edited (its
# Reviewer's value in the pledge table's column, R46 Part J).
HUB_PLEDGE = "/hub-pledge"
# The same tab with the pledge To review, before a decision.
HUB_PLEDGE_FRESH = "/hub-pledge-fresh"
HOME_CHANGE = {
    "id": HOME_ID,
    "entity_kind": "family",
    "entity_key": "1234",
    "field": "home_address",
    "handling": "manual",
    "baseline_value": HOME,
    "current_value": HOME,
    "submitted_value": NEW_HOME,
    "member_name": None,
}


def acting(**values):
    """The hub's census with the Family tab's home address change acting."""
    return family_tab(census_worklist(HOME_CHANGE | values))


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
    """The Administrator's page, its sorts and filters, Staff's and unavailable ones."""
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
        LATER: (
            TIMELINE,
            {
                "full": True,
                "query": TimelineQuery(start="2026-10-04", zone="America/Los_Angeles"),
                "values": {"size": "all", "sort": "-when"},
            },
        ),
        # Staff see the full timeline and export it (#1774, #1799) but may
        # not open Mail message pages.
        STAFF: (REVIEWED, {"full": True, "mail_links": False}),
        LIVE: (REVIEWED, {"full": True}),
        UNAVAILABLE: (
            TIMELINE,
            {"full": True, "testing_codes": True, "family_test_url": ADMIN},
        ),
        LOCKED: (
            TIMELINE,
            {"full": True, "eligible": False, "export": EXPORT | {"mutable": False}},
        ),
    }
    pages |= {
        path: (
            TIMELINE,
            {"full": True, "parameters": {"tab": key}} | STREAMS.get(key, {}),
        )
        for key, path in TAB.items()
    }
    pages |= {
        path: (
            TIMELINE,
            {
                "full": True,
                "parameters": {"tab": "family"},
                "census": census,
                "edit_today": "2054-10-05",
            },
        )
        for path, census in (
            (HUB_ACTIONS, acting()),
            (HUB_ACCEPTED, acting(decision="approved")),
        )
    }
    pages |= {
        path: (
            TIMELINE,
            {"full": True, "parameters": {"tab": "financial"}, "hub_financial": tab},
        )
        for path, tab in (
            (HUB_PLEDGE, pledge_tab()),
            (HUB_PLEDGE_FRESH, pledge_tab(fresh=True)),
        )
    }
    responses = {}
    with using("us_long"):
        for path, (timeline, options) in pages.items():
            # Administrators and Staff both may see Family codes.
            options = {
                "show_codes": True,
                "export": EXPORT,
                "mail_links": True,
                "sections": HUB,
                "census": CENSUS,
                "ministry": True,
            } | options
            responses[path] = ("text/html", render(context, admin, timeline, **options))
    return responses
