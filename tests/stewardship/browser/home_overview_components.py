"""Home as a busy Administrator's overview dashboard (#1673).

``PATH`` is an Administrator's Home on an active Production campaign with
every module on: two System health problems, failed background tasks,
automation notices to acknowledge, a Family participation row and every
campaign-end list with its count. The fold test reads it at 1366 x 768,
with the sidebar, and at 390 px.
"""

from datetime import date, timedelta
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import admin_context, admin_navigation
from parishkit.stewardship.accounts.admin_dashboard import campaign_facts, family_tiles
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.campaigns.domain import Percentage

PATH = "/home-overview"
CAMPAIGN = SimpleNamespace(
    pk=UUID(int=1673),
    state="active",
    delivery_paused=False,
    ever_active=True,
    structural_locked=True,
    active_configuration=SimpleNamespace(
        name="Stewardship 2027",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 10, 31),
        timezone="America/New_York",
        values={"modules": ["financial", "ministry", "census"]},
    ),
)
# Home's Family participation counts, as admin_dashboard.summary reads them.
FAMILIES = {
    "active": 1031,
    "participation": Percentage(402, 1012),
    "responded": 418,
    "active_responded": 412,
    "recent": 12,
}
# The menu's open counts, as the chrome reads them.
COUNTS = {
    "census_changes": 4,
    "ministry_roster": 12,
    "information_queue": 3,
    "ministry_report": 5,
}


def components(context, admin):
    """The overview page, rendered by the real Home template and menu code."""
    from parishkit.stewardship.source.refresh_status import FullRefreshStatus
    from parishkit.stewardship.system_health import Problem, Subject

    now = context["server_now"]
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
    chrome = admin | {
        "sections": sections,
        "breadcrumbs": [],
        "delivery_unknown": 2,
        "testing": False,
        "entries": {item.name: item for item in items},
    }
    failed = now - timedelta(hours=2)
    dashboard = {
        "campaign": CAMPAIGN,
        "campaign_facts": campaign_facts(CAMPAIGN, now),
        "refreshed_at": now - timedelta(hours=3),
        "full_refresh": FullRefreshStatus(
            now - timedelta(days=1),
            failed,
            False,
            failed_task_id=UUID(int=7),
            data_as_of=now - timedelta(hours=3),
            next_full_at=now + timedelta(hours=14),
        ),
        "can_view_task": True,
        "can_refresh": True,
        "refresh_key": "component-key",
        "health_problems": (
            Problem("sender_halted", (Subject("mail-dispatch", "mail"),), now),
            Problem("source_failed", at=failed),
        ),
        "health_severity": "error",
        "next_mail": {"kind": "reminder", "due_at": now + timedelta(days=2)},
        "families": FAMILIES,
        "family_tiles": family_tiles(FAMILIES, recent=FAMILIES["recent"]),
        "failed_tasks": 3,
        "automation_pending": 2,
    }
    html = render_to_string(
        "stewardship/home.html",
        context
        | {
            "admin_chrome": chrome,
            "principal": actor,
            "configuration": configuration,
            "dashboard": dashboard,
        },
    )
    return {PATH: ("text/html", html)}
