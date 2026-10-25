"""Review one at a time's pages with the real templates (#2123).

``START_PATH`` is a Staff member's start page with two unfinished runs,
every stream offered and Pledges greyed out with the menu's reason.
``PAGE_PATHS`` are one run's three pages: a Family page first, a Member
page and the last page, so the navigation rows can be compared across
pages. ``CARD_PATH`` is My review work with one unfinished run, whose card
continues it.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports import review_wizard_views as views
from parishkit.stewardship.reports.census_change_views import KIND_LABELS, ROUTE_LABELS
from parishkit.stewardship.reports.census_changes import CensusQuery
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
from parishkit.stewardship.reports.review_streams import EVERY, STREAMS
from parishkit.stewardship.reports.review_wizard import SORTS, WAITING

from .review_work_components import CAMPAIGN, COUNTS, chrome

START_PATH = "/review-wizard"
PAGE_PATHS = ("/review-wizard-1", "/review-wizard-2", "/review-wizard-3")
CARD_PATH = "/review-wizard-card"
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
                    (EVERY, "Every stage"),
                ],
                "stage": WAITING,
                "search": "",
                "context": CONTEXTS[stream.key] if available else {},
            }
        )
    used = datetime(2054, 10, 12, 18, 30, tzinfo=UTC)
    runs = [
        {
            "id": RUN,
            "stream": "Census changes",
            "filters": "Waiting",
            "progress": "Family 12 of 87, Member 2 of 3",
            "updated_at": used,
            "url": "/admin/reports/review/40/?run=" + str(RUN),
            "label": "Census changes, Family 12 of 87, Member 2 of 3",
        },
        {
            "id": UUID(int=2124),
            "stream": "Ministry requests",
            "filters": "Every stage, with a name search",
            "progress": "Member 3 of 9",
            "updated_at": used,
            "url": "/admin/reports/review/3/?run=" + str(UUID(int=2124)),
            "label": "Ministry requests, Member 3 of 9",
        },
    ]
    return {
        "runs": runs,
        "streams": streams,
        "chosen": "census",
        "single": False,
        "mode": "basic",
        "sorts": SORTS,
        "sort": "name",
        "start_url": "/admin/reports/review/",
    }


def page_context(position):
    """One of the run's three pages: a Family page, a Member page, the last."""
    census = STREAMS[0]
    family = {
        "family_id": UUID(int=7),
        "family_name": "Squyres, Jeff and Tracy",
        "family_duid": 4123,
        "member": None,
    }
    heading = family
    if position > 1:
        heading = family | {
            "member": {"name": "Squyres, Jeff", "duid": 77001, "new": position == 3}
        }
        if position == 3:
            heading["member"]["duid"] = None
    words = (
        "Family 1 of 1",
        "Family 1 of 1, Member 1 of 2",
        "Family 1 of 1, Member 2 of 2",
    )
    return {
        "run": SimpleNamespace(pk=RUN),
        "stream": census,
        "heading": heading,
        "progress": words[position - 1],
        "position": position,
        "size": 3,
        "previous_url": views.page_url(RUN, position - 1) if position > 1 else None,
        "next_url": views.page_url(RUN, position + 1) if position < 3 else None,
        "here_url": views.page_url(RUN, position),
        "focus": {key.replace("-", "_"): False for key in views.FROM},
        "stream_url": "/admin/reports/census/",
        "timeline_url": "/admin/reports/families/" + str(UUID(int=7)) + "/",
        "start_url": "/admin/reports/review/",
    }


def components(context, admin):
    """The start page, a run's three pages and My review work's card."""
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
    runs = [("Census changes, Family 12 of 87", views.page_url(RUN, 12))]
    pages[CARD_PATH] = (
        "text/html",
        render_to_string(
            "stewardship/my-review-work.html",
            base | {"review": {"campaign": CAMPAIGN, "counts": COUNTS, "runs": runs}},
        ),
    )
    return pages
