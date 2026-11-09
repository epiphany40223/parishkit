"""My review work with the real menu code (#1690).

``PATH`` is a Staff member's landing page on an active campaign without
the financial module, so Pledges is greyed out with the menu's reason,
with counts on every other stream (one in the thousands, to check a wide
count): the menu's read by stage (``MENU_STAGES``) and My review work's
own (``COUNTS``), one Ministry request flagged Not seen in ParishSoft.
``LEADER_PATH`` is a Ministry leader's two steps (#2405), with two
Ministries, one long-named, and 3 requests To review;
``LEADER_EMPTY_PATH`` the same with nothing To review,
``LEADER_LIMIT_PATH`` the same with a Step 1 file too large for a PDF, and
``LEADER_GREYED_PATH`` a campaign without Ministries, whose menu greys
both of the leader's pages out. A leader's menu badge for Review in bulk is
the cards' count, as on a real page. ``HOME_PATH`` is an Administrator's Home
with its Review work panel.
"""

from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import admin_context, admin_navigation, review_work
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.pipeline import Stream

PATH, LEADER_PATH, HOME_PATH = (
    "/review-work",
    "/review-work-leader",
    "/review-work-home",
)
LEADER_EMPTY_PATH = "/review-work-leader-empty"
LEADER_GREYED_PATH = "/review-work-leader-greyed"
LEADER_LIMIT_PATH = "/review-work-leader-limit"
# The Staff page after someone else accepted one census change (#2174): its
# To review card counts one fewer, what the page's check is answered with.
LATER_PATH = "/review-work-later"
CAMPAIGN = SimpleNamespace(
    pk=UUID(int=1690),
    state="active",
    delivery_paused=False,
    ever_active=True,
    structural_locked=True,
    active_configuration=SimpleNamespace(
        name="Stewardship 2027",
        start_date=None,
        end_date=None,
        timezone="America/New_York",
        values={"modules": ["ministry", "census"]},
    ),
)
MENU_STAGES = {
    "census_changes": {"to_review": 1234, "conflict": 2, "ready_to_update": 5},
    "ministry_report": {"to_review": 8, "to_enter_by_hand": 3},
    "limitations_report": {"to_review": 4},
    "information_queue": {"to_review": 6, "to_handle": 1},
}
COUNTS = {
    Stream.CENSUS: {},
    Stream.MINISTRY: {"not_seen": 1},
    Stream.CANNOT_PARTICIPATE: {},
    Stream.FINANCIAL: {},
    Stream.ADDITIONAL_INFORMATION: {},
    "leader": None,
}
LEADER = {
    "ministries": [
        {
            "duid": 12,
            "name": "Extraordinary Ministers of Holy Communion",
            "in_campaign": True,
            "join": 2,
            "leave": 1,
        },
        {"duid": 9, "name": "Food pantry", "in_campaign": True, "join": 0, "leave": 0},
    ]
}
# The same leader with nothing To review.
LEADER_EMPTY = {
    "ministries": [
        ministry | {"join": 0, "leave": 0} for ministry in LEADER["ministries"]
    ]
}
# A campaign that asks about no Ministry: the menu greys a leader's pages.
NO_MINISTRIES = SimpleNamespace(
    **vars(CAMPAIGN)
    | {
        "active_configuration": SimpleNamespace(
            **vars(CAMPAIGN.active_configuration) | {"values": {"modules": ["census"]}}
        )
    }
)


def chrome(admin, actor, menu_counts, stages=None, campaign=CAMPAIGN):
    """The chrome the context processor would build for ``actor``, its menu
    read by stage ``stages`` (``MENU_STAGES`` by default), on ``campaign``
    (``CAMPAIGN`` by default)."""
    stages = MENU_STAGES if stages is None else stages
    configuration = SimpleNamespace(mode="production")
    # ParishSoft data is present, so the menu runs no query (it would need a DB).
    items = admin_context._navigation_items(
        actor,
        "administrator" in actor.roles,
        campaign,
        configuration,
        parishsoft_data=True,
    )
    sections, _breadcrumbs = admin_navigation.build(
        SimpleNamespace(
            url_name="index", namespace=admin_navigation.NAMESPACE, kwargs={}
        ),
        items,
    )
    for section in sections:
        for entry in section["items"]:
            entry["count"] = menu_counts.get(entry["name"]) if entry["url"] else None
            entry["stages"] = stages.get(entry["name"]) if entry["url"] else None
            entry["count_words"] = admin_context.count_words(entry["name"])
    # Staff and leaders see My review work as Review work's first entry, as
    # the context processor draws it (R39 finding 4).
    lands = review_work.lands_here(actor)
    home_in_review = lands and admin_context._home_in_review(
        sections, review_work.home_label(actor), True
    )
    return configuration, admin | {
        "admin": "administrator" in actor.roles,
        "home_label": review_work.home_label(actor),
        "home_current": True,
        "home_in_review": home_in_review,
        "sections": sections,
        "entries": {item.name: item for item in items},
        "breadcrumbs": [],
    }


def bulk_badge(counts):
    """The menu's Ministry requests count for a page's ``counts``.

    A leader's is their requests To review, the cards' count, as the real
    menu's and ``my_ministries``' reads agree (#2405); Staff's is 8.
    """
    if "leader" not in counts:
        return 8
    mine = counts["leader"]
    if mine is None:
        return None
    return sum(item["join"] + item["leave"] for item in mine["ministries"])


def components(context, admin):
    """The landing page for Staff and a leader, and an Administrator's Home."""
    pages = {}
    later = MENU_STAGES | {
        "census_changes": MENU_STAGES["census_changes"] | {"to_review": 1233}
    }
    leader = ({"ministry_leader"}, frozenset({9, 12}))
    staff = ({"staff"}, frozenset())
    for path, (roles, scope), counts, stages, campaign, pdf_limit in (
        (PATH, staff, COUNTS, None, CAMPAIGN, None),
        (LEADER_PATH, leader, {"leader": LEADER}, None, CAMPAIGN, None),
        (LEADER_EMPTY_PATH, leader, {"leader": LEADER_EMPTY}, None, CAMPAIGN, None),
        (LEADER_LIMIT_PATH, leader, {"leader": LEADER}, None, CAMPAIGN, 1000),
        (LEADER_GREYED_PATH, leader, {"leader": None}, None, NO_MINISTRIES, None),
        (LATER_PATH, staff, COUNTS, later, CAMPAIGN, None),
    ):
        actor = Principal(UUID(int=1690), frozenset(roles), scope)
        configuration, page_chrome = chrome(
            admin, actor, {"ministry_report": bulk_badge(counts)}, stages, campaign
        )
        pages[path] = (
            "text/html",
            render_to_string(
                "stewardship/my-review-work.html",
                context
                | {
                    "admin_chrome": page_chrome,
                    "principal": actor,
                    "configuration": configuration,
                    "review": {
                        "campaign": campaign,
                        "counts": counts,
                        "pdf_limit": pdf_limit,
                    },
                },
            ),
        )
    actor = Principal(UUID(int=1), frozenset({"administrator"}))
    configuration, home_chrome = chrome(admin, actor, {"ministry_report": 8})
    pages[HOME_PATH] = (
        "text/html",
        render_to_string(
            "stewardship/home.html",
            context
            | {
                "admin_chrome": home_chrome,
                "principal": actor,
                "configuration": configuration,
                "dashboard": {
                    "campaign": CAMPAIGN,
                    "refreshed_at": None,
                    "review": {"campaign": CAMPAIGN, "counts": COUNTS},
                },
            },
        ),
    )
    return pages
