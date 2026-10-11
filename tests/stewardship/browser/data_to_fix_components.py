"""Data to fix in ParishSoft (#1011), with its production template and real menu.

The page is served twice at fixture paths, with the menu the real navigation
code builds for each role: ``PATH`` as an Administrator sees it (all four
sections: two counted, one at zero and one greyed out with its reason), and
``STAFF_PATH`` as Staff see it (only the two sections Staff may open, and
the Parish data group with this one entry). The section rows are built from
the page's own section list, so the order and the link names are the page's.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import admin_context, admin_navigation
from parishkit.stewardship.accounts.data_to_fix_views import visible_sections
from parishkit.stewardship.accounts.policy import Principal

PATH = "/data-to-fix-administrator"
STAFF_PATH = "/data-to-fix-staff"
CAMPAIGN = SimpleNamespace(
    pk=UUID(int=1011),
    state="active",
    ever_active=True,
    structural_locked=True,
    active_configuration=SimpleNamespace(
        values={"modules": ["financial", "ministry"]}, name="Stewardship 2027"
    ),
)
# What each section shows: two counted, one at zero, and census greyed out
# because this campaign does not include it (its menu entry's reason).
ANSWERS = {
    "form_cannot_open": {"count": 2},
    "refused_addresses": {"count": 3, "families": 2},
    "unreachable_families": {"count": 0},
    "census_changes": {"reason": "This campaign does not include the census."},
}


def _rows(principal):
    """The page's rows for ``principal``, as ``read_sections`` shapes them."""
    rows = []
    for section in visible_sections(principal):
        answer = ANSWERS[section.key]
        url = None
        if "count" in answer:
            url = reverse(f"admin:{section.page}") + section.query
        rows.append({"key": section.key, "label": section.label, "url": url} | answer)
    return rows


def components(context, admin):
    """The page as an Administrator and as Staff see it."""
    served = {}
    for path, roles in (
        (PATH, {"administrator", "staff", "ministry_leader"}),
        (STAFF_PATH, {"staff"}),
    ):
        actor = Principal(UUID(int=1), frozenset(roles))
        items = admin_context._navigation_items(
            actor,
            "administrator" in roles,
            CAMPAIGN,
            SimpleNamespace(mode="production"),
        )
        match = SimpleNamespace(
            url_name="parishsoft_fixes", namespace=admin_navigation.NAMESPACE, kwargs={}
        )
        sections, breadcrumbs = admin_navigation.build(match, items)
        rows = _rows(actor)
        html = render_to_string(
            "stewardship/data-to-fix.html",
            context
            | {
                "admin_chrome": admin
                | {
                    "sections": sections,
                    "breadcrumbs": breadcrumbs,
                    "admin": "administrator" in roles,
                },
                "campaign": CAMPAIGN,
                "refreshed_at": datetime(2026, 10, 11, 9, 30, tzinfo=UTC),
                "sections": rows,
                "keys": [row["key"] for row in rows],
                "nothing_to_fix": False,
                "can_refresh": "administrator" in roles,
            },
        )
        served[path] = ("text/html", html)
    return served
