"""The Admin menu built by the real navigation code (ADM-12.02 to .04).

An Administrator's menu for a Testing draft, on the Pages and emails page:
the Campaign setup group holds the current page, and two entries are greyed
out with their reasons (Production activation in the current group, Delivery
controls in Mail and Family portal). The menu's links are real Admin paths;
the browser tests only hover, focus, tap and collapse, never follow them.
"""

from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts import admin_context, admin_navigation
from parishkit.stewardship.accounts.policy import Principal

PATH = "/admin-menu"
CAMPAIGN = SimpleNamespace(
    pk=UUID(int=522),
    state="draft",
    ever_active=False,
    structural_locked=False,
    active_configuration=SimpleNamespace(values={"modules": ["financial", "ministry"]}),
)


def components(context, admin):
    """Render Home's layout with the real menu for a Testing draft."""
    actor = Principal(UUID(int=1), frozenset({"administrator"}))
    items = admin_context._navigation_items(
        actor, True, CAMPAIGN, SimpleNamespace(mode="testing")
    )
    match = SimpleNamespace(
        url_name="content_catalog",
        namespace=admin_navigation.NAMESPACE,
        kwargs={"campaign_id": CAMPAIGN.pk},
    )
    sections, breadcrumbs = admin_navigation.build(match, items)
    chrome = admin | {"sections": sections, "breadcrumbs": breadcrumbs}
    html = render_to_string(
        "stewardship/home.html",
        context | {"configuration": {"mode": "testing"}, "admin_chrome": chrome},
    )
    return {PATH: ("text/html", html)}
