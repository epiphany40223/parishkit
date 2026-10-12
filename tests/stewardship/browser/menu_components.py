"""The Admin menu built by the real navigation code (ADM-12.02 to .04).

An Administrator's menu for a Testing draft, on the Pages and emails page:
the Campaign setup group holds the current page, and two entries are greyed
out with their reasons (Production activation in the current group, Delivery
controls in Mail and Family portal). The menu's links are real Admin paths.
Two of them are served too, so the scroll-position tests (#620) can follow
them: Pages and emails (near the menu's top) as the same page as ``PATH``,
and System logs (near its end) as the menu on System logs. The other links
are never followed. ``LOGO_PATH`` is the same page with a parish logo in the
header, served at ``LOGO``, and ``FAMILY_LOGO_PATH`` the Family sign-in page
with that logo, for the header-height test (#638). ``WIDE_PATH`` and
``FAMILY_WIDE_PATH`` are the same two pages with a 4:1 wordmark, the widest
menu logo (#1062), served at ``WIDE_LOGO``. Each logo carries its drawn size
as ``branding_for`` gives it. Additional information and Ministry follow-up
carry open counts (``COUNTS``, #585).
"""

from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from PIL import Image

from parishkit.stewardship.accounts import admin_context, admin_navigation
from parishkit.stewardship.accounts.branding_context import header_logo_size
from parishkit.stewardship.accounts.policy import Principal

PATH = "/admin-menu"
LOGO_PATH = "/admin-menu-logo"
LOGO = "/branding/admin-menu-logo.png"
FAMILY_LOGO_PATH = "/family-login-logo"
WIDE_PATH = "/admin-menu-wordmark"
WIDE_LOGO = "/branding/admin-menu-wordmark.png"
FAMILY_WIDE_PATH = "/family-login-wordmark"
# The stored menu files: the square 128-pixel variant, and a 4:1 wordmark at
# the widest stored menu size (#1062).
LOGO_SIZE = (128, 128)
WIDE_SIZE = (512, 128)


def _branding(url, size):
    """The header's branding context for a menu logo of stored ``size``."""
    width, height = header_logo_size(*size)
    return {
        "parish_branding": {
            "name": "Saint Example Parish",
            "menu": url,
            "menu_width": width,
            "menu_height": height,
        }
    }


BRANDING = _branding(LOGO, LOGO_SIZE)
WIDE_BRANDING = _branding(WIDE_LOGO, WIDE_SIZE)


def _png(size):
    """A synthetic logo file of ``size`` pixels."""
    data = BytesIO()
    Image.new("RGB", size, "teal").save(data, format="PNG")
    return data.getvalue()


CAMPAIGN = SimpleNamespace(
    pk=UUID(int=522),
    state="draft",
    ever_active=False,
    structural_locked=False,
    active_configuration=SimpleNamespace(values={"modules": ["financial", "ministry"]}),
)

# Open counts on the two follow-up queues' entries (#585).
COUNTS = {"information_queue": 3, "ministry_followup": 1250}


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
        # The follow-up queues' open counts, as portal_chrome adds them (#585).
        for section in sections:
            for entry in section["items"]:
                entry["count"] = COUNTS.get(entry["name"])
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
        BRANDING,
    )
    _sections, wide = render(
        "content_catalog",
        {"campaign_id": CAMPAIGN.pk},
        WIDE_BRANDING,
    )
    url = {
        item["name"]: item["url"] for section in sections for item in section["items"]
    }
    return {
        PATH: ("text/html", html),
        url["content_catalog"]: ("text/html", html),
        url["logs"]: ("text/html", logs),
        LOGO_PATH: ("text/html", branded),
        LOGO: ("image/png", _png(LOGO_SIZE)),
        FAMILY_LOGO_PATH: (
            "text/html",
            render_to_string("stewardship/family-login.html", context | BRANDING),
        ),
        WIDE_PATH: ("text/html", wide),
        WIDE_LOGO: ("image/png", _png(WIDE_SIZE)),
        FAMILY_WIDE_PATH: (
            "text/html",
            render_to_string("stewardship/family-login.html", context | WIDE_BRANDING),
        ),
    }
