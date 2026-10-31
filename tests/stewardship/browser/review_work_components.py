"""My review work with the real menu code (#1690).

``PATH`` is a Staff member's landing page on an active campaign without
the financial module, so Pledges is greyed out with the menu's reason,
with counts on every other stream (one in the thousands, to check a wide
count): the menu's read by stage (``MENU_STAGES``) and My review work's
own (``COUNTS``), one Ministry request flagged Not seen in ParishSoft.
``LEADER_PATH`` is a Ministry leader's, with two Ministries, one
long-named. ``HOME_PATH`` is an Administrator's Home with its Review work
panel.
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


def chrome(admin, actor, menu_counts, stages=None):
    """The chrome the context processor would build for ``actor``, its menu
    read by stage ``stages`` (``MENU_STAGES`` by default)."""
    stages = MENU_STAGES if stages is None else stages
    configuration = SimpleNamespace(mode="production")
    # ParishSoft data is present, so the menu runs no query (it would need a DB).
    items = admin_context._navigation_items(
        actor,
        "administrator" in actor.roles,
        CAMPAIGN,
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


def components(context, admin):
    """The landing page for Staff and a leader, and an Administrator's Home."""
    pages = {}
    later = MENU_STAGES | {
        "census_changes": MENU_STAGES["census_changes"] | {"to_review": 1233}
    }
    for path, roles, scope, counts, stages in (
        (PATH, {"staff"}, frozenset(), COUNTS, None),
        (
            LEADER_PATH,
            {"ministry_leader"},
            frozenset({9, 12}),
            {"leader": LEADER},
            None,
        ),
        (LATER_PATH, {"staff"}, frozenset(), COUNTS, later),
    ):
        actor = Principal(UUID(int=1690), frozenset(roles), scope)
        configuration, page_chrome = chrome(
            admin, actor, {"ministry_report": 8}, stages
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
                    "review": {"campaign": CAMPAIGN, "counts": counts},
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
