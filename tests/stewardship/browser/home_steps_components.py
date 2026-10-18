"""Home's Next steps panel with the real menu code (ADM-12.21, NAV-18).

``PATH`` is an Administrator's Home on an active Production campaign with
the Ministry module off, so Ministry follow-up is greyed out with the menu's
reason, Additional information carries an open count and two emails may not
have arrived. ``PHONE_PATH`` is the same page; the test reads it at 320 px.
``CLOSED_PATH`` and ``LEADER_PATH`` are a Staff member's and a Ministry
leader's Home on a closed campaign with the Ministry module and the census
(#1590): the campaign-end lists, each with its count.
"""

from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import admin_context, admin_navigation
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.campaigns.domain import Percentage

PATH = "/home-next-steps"
CAMPAIGN = SimpleNamespace(
    pk=UUID(int=1522),
    state="active",
    delivery_paused=False,
    ever_active=True,
    structural_locked=True,
    active_configuration=SimpleNamespace(
        name="Stewardship 2027",
        start_date=None,
        end_date=None,
        timezone="America/New_York",
        values={"modules": ["financial"]},
    ),
)
COUNTS = {"information_queue": 3}
CLOSED_PATH = "/home-next-steps-closed"
LEADER_PATH = "/home-next-steps-leader"
CLOSED = SimpleNamespace(
    **(
        vars(CAMPAIGN)
        | {
            "state": "closed",
            "active_configuration": SimpleNamespace(
                **(
                    vars(CAMPAIGN.active_configuration)
                    | {"values": {"modules": ["financial", "ministry", "census"]}}
                )
            ),
        }
    )
)
CLOSED_COUNTS = {"census_changes": 4, "information_queue": 3, "ministry_report": 2}


def components(context, admin):
    """Home as the index view renders it for an Administrator, with Next steps."""
    actor = Principal(UUID(int=1), frozenset({"administrator"}))
    configuration = SimpleNamespace(mode="production")
    # ParishSoft data is present, so the menu runs no query (it would need a DB).
    items = admin_context._navigation_items(
        actor, True, CAMPAIGN, configuration, parishsoft_data=True
    )
    sections, _breadcrumbs = admin_navigation.build(
        SimpleNamespace(
            url_name="index", namespace=admin_navigation.NAMESPACE, kwargs={}
        ),
        items,
    )
    for section in sections:
        for entry in section["items"]:
            entry["count"] = COUNTS.get(entry["name"]) if entry["url"] else None
    chrome = admin | {"sections": sections, "breadcrumbs": [], "delivery_unknown": 2}
    html = render_to_string(
        "stewardship/home.html",
        context
        | {
            "admin_chrome": chrome,
            "principal": actor,
            "configuration": configuration,
            "dashboard": {
                "campaign": CAMPAIGN,
                "refreshed_at": None,
                "families": {
                    "active": 400,
                    "participation": Percentage(40, 400),
                    "responded": 160,
                    "recent": 12,
                },
            },
        },
    )
    return (
        {PATH: ("text/html", html)}
        | health_components(context, admin, chrome, actor, configuration)
        | closed_components(context, admin)
    )


def closed_components(context, admin):
    """Home on a closed campaign for Staff and for a Ministry leader (#1590).

    The menu is each role's real one, with the counts the chrome would read;
    Staff also get Roster changes to enter's count, as Home's dashboard
    reads it.
    """
    configuration = SimpleNamespace(mode="production")
    pages = {}
    for path, actor in (
        (CLOSED_PATH, Principal(UUID(int=2), frozenset({"staff"}))),
        (
            LEADER_PATH,
            Principal(UUID(int=3), frozenset({"ministry_leader"}), frozenset({9})),
        ),
    ):
        items = admin_context._navigation_items(
            actor, False, CLOSED, configuration, parishsoft_data=True
        )
        sections, _breadcrumbs = admin_navigation.build(
            SimpleNamespace(
                url_name="index", namespace=admin_navigation.NAMESPACE, kwargs={}
            ),
            items,
        )
        for section in sections:
            for entry in section["items"]:
                entry["count"] = (
                    CLOSED_COUNTS.get(entry["name"]) if entry["url"] else None
                )
        chrome = admin | {"admin": False, "sections": sections, "breadcrumbs": []}
        staff = "staff" in actor.roles
        pages[path] = (
            "text/html",
            render_to_string(
                "stewardship/home.html",
                context
                | {
                    "admin_chrome": chrome,
                    "principal": actor,
                    "configuration": configuration,
                    "dashboard": {
                        "campaign": CLOSED,
                        "refreshed_at": None,
                        "roster_todo": 5 if staff else None,
                    },
                },
            ),
        )
    return pages


# Home with System health problems and a critical-problems banner (#1214):
# an error, two warnings (one Home also reports in its refresh block), and
# a banner group whose problem is current.
HEALTH_PATH = "/home-health"
# Home with the shared kinds of #1214's slice 1: an unknown No-reminders
# WorkGroup while Reminders are scheduled, a backup key that cannot be
# checked, a requested backup never picked up, and (Production) off-site
# copies not set up.
SHARED_PATH = "/home-health-shared"


def health_components(context, admin, chrome, actor, configuration):
    """Home as an Administrator sees it while System health lists problems."""
    from datetime import timedelta

    from parishkit.stewardship.accounts.backup_destination import OffsiteStatus
    from parishkit.stewardship.audit.critical_events import summary
    from parishkit.stewardship.source.refresh_status import FullRefreshStatus
    from parishkit.stewardship.system_health import Problem, Subject

    now = context["server_now"]
    failed = now - timedelta(hours=2)
    problems = (
        Problem("sender_halted", (Subject("mail-dispatch", "mail"),), now),
        Problem("source_failed", at=failed),
        Problem("backup_offsite_failed", at=now - timedelta(hours=5)),
    )
    banner = chrome | {
        "critical_count": 2,
        "critical_limit": 500,
        "critical_shown": "shown",
        "critical_events": summary({"source_destructive_change": 1, "task_failed": 1}),
    }
    html = render_to_string(
        "stewardship/home.html",
        context
        | {
            "admin_chrome": banner,
            "principal": actor,
            "configuration": configuration,
            "dashboard": {
                "campaign": CAMPAIGN,
                "refreshed_at": None,
                "full_refresh": FullRefreshStatus(
                    now - timedelta(days=1), failed, False, failed_task_id=UUID(int=7)
                ),
                "can_view_task": True,
                "can_refresh": True,
                "refresh_key": "component-key",
                "health_problems": problems,
                "health_listed": {"failed": True, "late": False, "offsite": True},
                "offsite": OffsiteStatus(
                    "failed", "Copying the newest backup failed.", now, None, None
                ),
            },
        },
    )
    shared = (
        Problem(
            "workgroup_missing",
            workgroup="Do not remind",
            detail="reminders_scheduled",
        ),
        Problem("backup_key_unchecked"),
        Problem(
            "backup_request_failed",
            at=now - timedelta(hours=1),
            detail="not_picked_up",
        ),
        Problem("offsite_missing"),
    )
    shared_html = render_to_string(
        "stewardship/home.html",
        context
        | {
            "admin_chrome": chrome,
            "principal": actor,
            "configuration": configuration,
            "dashboard": {
                "campaign": CAMPAIGN,
                "refreshed_at": None,
                "health_problems": shared,
                "health_listed": {
                    "failed": False,
                    "late": False,
                    "offsite": False,
                    "offsite_unset": True,
                },
                "offsite_unset": True,
            },
        },
    )
    return {
        HEALTH_PATH: ("text/html", html),
        SHARED_PATH: ("text/html", shared_html),
    }
