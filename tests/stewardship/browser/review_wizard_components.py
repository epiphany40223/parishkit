"""Review one at a time's pages with the real templates (#2123).

``START_PATH`` is a Staff member's start page, every stream offered and
Pledges greyed out with the menu's reason (it lists no runs, R52 Part W).
``PAGE_PATHS`` are one run's three pages, each of a different height, so
the navigation rows can be compared across pages: a Family page first, a
Member page and the last page, a New Member's. Each draws its census
section through the shared component with Census changes' row actions:
the Family page its home address change To review with one change
filtered out, the Member page a deceased Member's two changes, the New
Member page its addition and the Family's answers for the Member.
``ACCEPTED_PATH`` and ``MEMBER_ACCEPTED_PATH`` are the Family and Member
pages once a change is accepted (a test serves each at its page's
address, which the section redraws from). ``FOCUS_PATH`` is the Member
page opened by the bottom row's Next, ``BACK_PATH`` by the top row's
Previous. ``GREYED_CARD_PATH`` is My review work while the menu greys
the wizard out, and ``NO_CARD_PATH`` while it has no wizard entry.
``PLEDGE_PATH`` is a Pledges page, its pledge To review with the Family
hub's Pledges row actions, and ``PLEDGE_EDITED_PATH`` the same page once
the pledge is edited (#2123).
"""

from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports import review_wizard_views as views
from parishkit.stewardship.reports.census_change_views import KIND_LABELS, ROUTE_LABELS
from parishkit.stewardship.reports.census_changes import CensusQuery
from parishkit.stewardship.reports.hub_actions import census_subject
from parishkit.stewardship.reports.hub_census import shape as hub_shape
from parishkit.stewardship.reports.information import InformationQuery
from parishkit.stewardship.reports.information_worklist import (
    WorklistQuery as InformationWorklist,
)
from parishkit.stewardship.reports.limitation_worklist import (
    WorklistQuery as LimitationsWorklist,
)
from parishkit.stewardship.reports.ministry_worklist import MEMBERS
from parishkit.stewardship.reports.ministry_worklist import (
    WorklistQuery as MinistryWorklist,
)
from parishkit.stewardship.reports.review_sections import Part, Section
from parishkit.stewardship.reports.review_streams import EVERY, STREAMS
from parishkit.stewardship.reports.review_wizard import SORTS, WAITING

from ..test_hub_actions import census_worklist, hub_rows, pledge_tab
from ..test_hub_census import ANSWERS, NAMES
from .review_work_components import CAMPAIGN, COUNTS, chrome
from .timeline_components import HOME_CHANGE

START_PATH = "/review-wizard"
PAGE_PATHS = ("/review-wizard-1", "/review-wizard-2", "/review-wizard-3")
GREYED_CARD_PATH = "/review-wizard-card-greyed"
NO_CARD_PATH = "/review-wizard-card-none"
# The menu's reason while it greys Review one at a time out.
GREYED_REASON = "No stream is offered to you in this campaign."
ACCEPTED_PATH = "/review-wizard-1-accepted"
MEMBER_ACCEPTED_PATH = "/review-wizard-2-accepted"
FOCUS_PATH = "/review-wizard-2-from-next"
BACK_PATH = "/review-wizard-2-from-previous"
PLEDGE_PATH = "/review-wizard-pledge"
PLEDGE_EDITED_PATH = "/review-wizard-pledge-edited"
# The edited pledge page with every amount at $99,999.99 (#2258).
PLEDGE_WIDE_PATH = "/review-wizard-pledge-wide"
# The hub unit tests' deceased Member 5, its two changes, and the Member the
# Family added, with the ids their census worklist items carry.
DECEASED = "11111111-0000-4000-8000-000000000005"
DEATH_DATE = "11111111-0000-4000-8000-000000000006"
ADDED = "6f1c1b6e-0000-4000-8000-000000000001"
ADDITION = "11111111-0000-4000-8000-000000000008"
MEMBER_CHANGES = {
    DECEASED: {
        "entity_key": "5",
        "field": "deceased_status",
        "handling": "manual",
        "baseline_value": False,
        "current_value": False,
        "submitted_value": True,
        "member_name": "Adams, Eve",
    },
    DEATH_DATE: {
        "entity_key": "5",
        "field": "death_date",
        "handling": "manual",
        "baseline_available": False,
        "baseline_value": None,
        "current_value": None,
        "submitted_value": "2026-09-01",
        "member_name": "Adams, Eve",
    },
}
RUN = UUID(int=2123)
STAFF = Principal(UUID(int=2123), frozenset({"staff"}))
CONTEXTS = {
    "census": {
        "query": CensusQuery(),
        "route_choices": list(ROUTE_LABELS.items()),
        "kind_choices": list(KIND_LABELS.items()),
    },
    "ministry": {
        "query": MinistryWorklist(),
        "ministries": [
            {"duid": 9, "name": "Food pantry", "in_campaign": True},
            {"duid": 12, "name": "Choir", "in_campaign": True},
        ],
        "members_choices": MEMBERS,
    },
    "pledges": {"search": ""},
    "information": {
        "query": InformationWorklist(information=InformationQuery(disposition="all"))
    },
    "limitations": {"query": LimitationsWorklist()},
}


def start_context():
    """The start page's context as ``review_wizard_views._start_context`` builds it."""
    streams = []
    for stream in STREAMS:
        available = stream.key != "pledges"
        streams.append(
            {
                "stream": stream,
                "label": stream.label,
                "reason": None
                if available
                else "This campaign does not include Financial stewardship",
                "available": available,
                "stages": [
                    (WAITING, "Waiting"),
                    *stream.stages(STAFF),
                    (EVERY, "Every stage except History"),
                ],
                "stage": WAITING,
                "search": "",
                "context": CONTEXTS[stream.key] if available else {},
            }
        )
    return {
        "streams": streams,
        "chosen": "census",
        "single": False,
        "mode": "basic",
        "sorts": SORTS,
        "sort": "name",
        "start_url": "/admin/reports/review/",
    }


def family_section(**values):
    """The Family page's census section: the home address change, acting.

    Shaped and decorated as ``review_sections`` does it, from the hub unit
    tests' rows: the hub's Family subject given Census changes' row actions
    under the wizard's prefix.
    """
    worklist = census_worklist(HOME_CHANGE | values, prefix="review-family-census")
    acted = census_subject(hub_shape(ANSWERS, NAMES, hub_rows()).family, worklist)
    return Section(
        listed=True,
        parts=(Part("census", "review-family", subject=acted),),
        dialogs=(acted.actions,),
        filtered_out=1,
    )


def member_section(**values):
    """The Member page's census section: Member 5's two changes, acting.

    ``values`` decorate the first change (its decision, say).
    """
    census = hub_shape(ANSWERS, NAMES, hub_rows())
    member = next(m for m in census.members if m.entity == ("member", "5"))
    items = [
        {"id": item} | change | (values if item == DECEASED else {})
        for item, change in MEMBER_CHANGES.items()
    ]
    acted = census_subject(
        member, census_worklist(*items, prefix="review-member-census")
    )
    return Section(
        listed=True,
        parts=(Part("census", "review-member", subject=acted),),
        dialogs=(acted.actions,),
    )


def new_member_section():
    """The New Member page's section: the addition To review and the
    Family's answers for the Member."""
    census = hub_shape(ANSWERS, NAMES, hub_rows())
    member = next(m for m in census.members if m.entity == ("proposed_member", ADDED))
    addition = {
        "id": ADDITION,
        "entity_kind": "proposed_member",
        "entity_key": ADDED,
        "field": "new_member",
        "handling": "manual",
        "baseline_available": False,
        "baseline_value": None,
        "current_value": None,
        "submitted_value": ANSWERS["proposed_members"][ADDED],
        "member_name": "Adams, Zed",
    }
    acted = census_subject(
        member, census_worklist(addition, prefix="review-member-census")
    )
    return Section(
        listed=True,
        parts=(Part("census", "review-member", subject=acted),),
        dialogs=(acted.actions,),
    )


def pledge_context(fresh, wide=False):
    """A Pledges page of a one-page run: the pledge To review (``fresh``)
    or edited, drawn as review_sections draws it, with the Financial tab's
    Pledges row actions (its pledge editor's frequencies too); ``wide``
    makes every amount $99,999.99 (``pledge_tab``)."""
    from parishkit.stewardship.reports.financial import FREQUENCY_LABELS
    from parishkit.stewardship.responses.financial import FREQUENCIES

    financial = pledge_tab(fresh=fresh, wide=wide)
    return page_context(1) | {
        "stream": next(stream for stream in STREAMS if stream.key == "pledges"),
        "previous_url": None,
        "next_url": None,
        "size": 1,
        "section": Section(
            listed=True,
            parts=(Part("pledge", "review-pledge", financial=financial),),
            dialogs=(financial.pledge.actions,),
        ),
        "frequencies": [(key, FREQUENCY_LABELS[key]) for key in FREQUENCIES],
    }


def page_context(position, focus=None, **values):
    """One of the run's three pages: a Family page, a Member page, the last.

    ``values`` decorate the page's first change (its decision, say), and
    ``focus`` names the navigation control the page was opened by. The
    Family has envelope 312; the Member page's name links to the hub's tab.
    """
    census = STREAMS[0]
    family = {
        "family_id": UUID(int=7),
        "family_name": "Squyres, Jeff and Tracy",
        "family_duid": 4123,
        "envelope": 312,
        "member": None,
    }
    heading = family
    if position > 1:
        # The Member pages' Family has no envelope: none is shown.
        heading = family | {
            "envelope": None,
            "member": {
                "name": "Squyres, Jeff",
                "duid": 77001 if position == 2 else None,
                "new": position == 3,
                "tab": f"member-{position}",
            },
        }
    timeline = "/admin/reports/families/" + str(UUID(int=7)) + "/"
    return {
        "run": SimpleNamespace(pk=RUN),
        "stream": census,
        "heading": heading,
        "member_url": f"{timeline}?tab=member-{position}" if position > 1 else None,
        # A grouped stream names only the Family's place (R52 Part W).
        "progress": "Family 1 of 1",
        "position": position,
        "size": 3,
        "previous_url": views.page_url(RUN, position - 1) if position > 1 else None,
        "next_url": views.page_url(RUN, position + 1) if position < 3 else None,
        "here_url": views.page_url(RUN, position),
        "focus": {key.replace("-", "_"): key == focus for key in views.FROM},
        "start_url": "/admin/reports/review/",
        "listed": True,
        "member_page": position > 1,
        "section": (family_section, member_section, new_member_section)[position - 1](
            **values
        ),
        "edit_today": "2026-10-24",
        "frequencies": [],
    }


def components(context, admin):
    """The start page, a run's three pages and My review work's pane."""
    configuration, page_chrome = chrome(admin, STAFF, {})
    base = context | {
        "admin_chrome": page_chrome,
        "principal": STAFF,
        "configuration": configuration,
    }
    pages = {
        START_PATH: (
            "text/html",
            render_to_string(
                "stewardship/review-wizard-start.html", base | start_context()
            ),
        )
    }
    for position, path in enumerate(PAGE_PATHS, 1):
        pages[path] = (
            "text/html",
            render_to_string(
                "stewardship/review-wizard-page.html", base | page_context(position)
            ),
        )
    pages[ACCEPTED_PATH] = (
        "text/html",
        render_to_string(
            "stewardship/review-wizard-page.html",
            base | page_context(1, decision="approved"),
        ),
    )
    pages[MEMBER_ACCEPTED_PATH] = (
        "text/html",
        render_to_string(
            "stewardship/review-wizard-page.html",
            base | page_context(2, decision="approved"),
        ),
    )
    for path, focus in ((FOCUS_PATH, "next-bottom"), (BACK_PATH, "previous-top")):
        pages[path] = (
            "text/html",
            render_to_string(
                "stewardship/review-wizard-page.html",
                base | page_context(2, focus=focus),
            ),
        )
    for path, fresh, wide in (
        (PLEDGE_PATH, True, False),
        (PLEDGE_EDITED_PATH, False, False),
        (PLEDGE_WIDE_PATH, False, True),
    ):
        pages[path] = (
            "text/html",
            render_to_string(
                "stewardship/review-wizard-page.html",
                base | pledge_context(fresh, wide),
            ),
        )
    review = {"campaign": CAMPAIGN, "counts": COUNTS}
    greyed = SimpleNamespace(
        label="Review one at a time", url=None, reason=GREYED_REASON
    )
    others = {
        name: item
        for name, item in page_chrome["entries"].items()
        if name != "review_wizard"
    }
    for path, entries in (
        (GREYED_CARD_PATH, others | {"review_wizard": greyed}),
        (NO_CARD_PATH, others),
    ):
        pages[path] = (
            "text/html",
            render_to_string(
                "stewardship/my-review-work.html",
                base
                | {
                    "admin_chrome": page_chrome | {"entries": entries},
                    "review": review,
                },
            ),
        )
    return pages
