"""Home's Next steps panel with the real menu code (ADM-12.21, NAV-18).

``PATH`` is an Administrator's Home on an active Production campaign with
the Ministry module off, so Ministry follow-up is greyed out with the menu's
reason, Additional information carries an open count and two emails may not
have arrived. ``PHONE_PATH`` is the same page; the test reads it at 320 px.
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


def components(context, admin):
    """Home as the index view renders it for an Administrator, with Next steps."""
    actor = Principal(UUID(int=1), frozenset({"administrator"}))
    configuration = SimpleNamespace(mode="production")
    items = admin_context._navigation_items(actor, True, CAMPAIGN, configuration)
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
    return {PATH: ("text/html", html)}
