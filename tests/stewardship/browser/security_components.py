"""The dashboard's security event panel, rendered from the production shaping.

For the in-place acknowledgements (#519 PR 4) the dashboard is also served at
``HOME`` and the fixture server answers each event's Acknowledge with a real
Post/Redirect/Get redirect back to it, one event fewer each time
(``POSTS``). The critical-problems banner's Acknowledge, which the server
answers by redirecting to Home from whatever page it was on, is answered
from ``BANNER`` with a redirect to a Home without the banner (``CLEARED``).
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.security_events import KINDS
from parishkit.stewardship.accounts.user_rows import role_labels

HOME = "/security-home"
BANNER, CLEARED = "/critical-banner", "/critical-cleared"
ACKNOWLEDGE = "/admin/security-events/{}/acknowledge"
# The fixture server's answers (status, Location, body): events 1, 2 and 3
# acknowledged in that order, then the banner.
POSTS = {
    ACKNOWLEDGE.format(UUID(int=1)): (303, HOME + "?left=2", ""),
    ACKNOWLEDGE.format(UUID(int=2)): (303, HOME + "?left=1", ""),
    ACKNOWLEDGE.format(UUID(int=3)): (303, HOME + "?left=0", ""),
    "/admin/critical-events/acknowledge": (303, CLEARED, ""),
}


def components(context, admin):
    """A dashboard with expansions to acknowledge, one already acknowledged."""
    moment = datetime(2026, 9, 21, 14, 30, 0, tzinfo=UTC)

    def event(index, kind, target, before, after):
        """One row as `open_events` shapes it."""
        return {
            "id": UUID(int=index),
            "kind": kind,
            "label": KINDS[kind],
            "target": target,
            "before": role_labels(before),
            "after": role_labels(after),
            "created_at": moment,
            "actor": "admin@example.org" if index != 3 else None,
        }

    events = [
        event(1, "administrator_granted", "new@example.org", [], ["administrator"]),
        event(
            2,
            "domain_staff_granted",
            "partner.example",
            ["ministry_leader"],
            ["ministry_leader", "staff"],
        ),
        # A recovery event has no portal actor, and its target is shown as text.
        event(3, "domain_created", "<b>partner.example</b>", [], ["staff"]),
    ]

    def page(rows, chrome=admin):
        """Render the dashboard as the index view does, with no campaign yet."""
        return render_to_string(
            "stewardship/home.html",
            context
            | {"admin_chrome": chrome}
            | {
                "configuration": SimpleNamespace(mode="testing"),
                "dashboard": {
                    "campaign": None,
                    "refreshed_at": None,
                    "security_events": rows,
                },
            },
        )

    # The critical-problems banner as every Admin page draws it.
    banner = admin | {
        "critical_count": 2,
        "critical_events": [{"label": "Source data failed checks", "count": 2}],
        "critical_shown": "signed",
        "critical_limit": 100,
    }
    return {
        "/dashboard-events": ("text/html", page(events)),
        "/dashboard-clear": ("text/html", page([])),
        HOME: ("text/html", page(events)),
        HOME + "?left=2": ("text/html", page(events[1:])),
        HOME + "?left=1": ("text/html", page(events[2:])),
        HOME + "?left=0": ("text/html", page([])),
        BANNER: ("text/html", page(events, banner)),
        CLEARED: ("text/html", page([])),
        # A refused Acknowledge (an altered or expired list), as the Admin
        # error page shows it: under the same chrome, banner included.
        "/critical-refused": (
            "text/html",
            render_to_string(
                "stewardship/error.html",
                context
                | {
                    "admin_chrome": banner,
                    "admin": True,
                    "title": "This request could not be completed",
                    "guidance": "Reload the page and try again.",
                    "error_messages": ["The submitted list was changed."],
                    "home": "/admin/",
                    "home_label": "Administration home",
                },
            ),
        ),
        # A banner that stays after Acknowledge: more problems than one
        # acknowledgement covers.
        "/critical-remaining": (
            "text/html",
            page([], banner | {"critical_count": 150, "critical_shown": "rest"}),
        ),
    }
