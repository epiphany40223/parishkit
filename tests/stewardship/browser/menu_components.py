"""The Admin menu built by the real navigation code (ADM-12.02 to .04).

An Administrator's menu for a Testing draft, on the Pages and emails page:
the Campaign setup group holds the current page, and two entries are greyed
out with their reasons (Production activation in the current group, Delivery
controls in Mail and Family portal). The menu's links are real Admin paths.
Two of them are served too, so the scroll-position tests (#620) can follow
them: Pages and emails (near the menu's top) as the same page as ``PATH``,
and System logs (near its end) as the menu on System logs. The other links
are never followed. ``LOGO_PATH`` is the same page with a parish logo in the
header, served at ``LOGO``, for the header-height test (#638).
"""

from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from PIL import Image

from parishkit.stewardship.accounts import admin_context, admin_navigation
from parishkit.stewardship.accounts.policy import Principal

PATH = "/admin-menu"
LOGO_PATH = "/admin-menu-logo"
LOGO = "/branding/admin-menu-logo.png"
CAMPAIGN = SimpleNamespace(
    pk=UUID(int=522),
    state="draft",
    ever_active=False,
    structural_locked=False,
    active_configuration=SimpleNamespace(values={"modules": ["financial", "ministry"]}),
)


def components(context, admin):
    """Render Home's layout with the real menu, on two of its pages.

    ``PATH`` and Pages and emails' own menu link serve the menu on Pages and
    emails; System logs' menu link serves the menu on System logs.
    """
    actor = Principal(UUID(int=1), frozenset({"administrator"}))
    items = admin_context._navigation_items(
        actor, True, CAMPAIGN, SimpleNamespace(mode="testing")
    )

    def render(url_name, kwargs, extra=None):
        """Home's layout with the menu marking ``url_name`` as current."""
        match = SimpleNamespace(
            url_name=url_name, namespace=admin_navigation.NAMESPACE, kwargs=kwargs
        )
        sections, breadcrumbs = admin_navigation.build(match, items)
        chrome = admin | {"sections": sections, "breadcrumbs": breadcrumbs}
        html = render_to_string(
            "stewardship/home.html",
            context
            | {"configuration": {"mode": "testing"}, "admin_chrome": chrome}
            | (extra or {}),
        )
        return sections, html

    sections, html = render("content_catalog", {"campaign_id": CAMPAIGN.pk})
    _sections, logs = render("logs", {})
    _sections, branded = render(
        "content_catalog",
        {"campaign_id": CAMPAIGN.pk},
        {"parish_branding": {"name": "Saint Example Parish", "menu": LOGO}},
    )
    # A square logo, like the stored 128-pixel menu variant.
    logo = BytesIO()
    Image.new("RGB", (128, 128), "teal").save(logo, format="PNG")
    url = {
        item["name"]: item["url"] for section in sections for item in section["items"]
    }
    return {
        PATH: ("text/html", html),
        url["content_catalog"]: ("text/html", html),
        url["logs"]: ("text/html", logs),
        LOGO_PATH: ("text/html", branded),
        LOGO: ("image/png", logo.getvalue()),
    }
