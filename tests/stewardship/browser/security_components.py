"""The dashboard's security event panel, rendered from the production shaping."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.security_events import KINDS
from parishkit.stewardship.accounts.user_rows import role_labels


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

    def page(rows):
        """Render the dashboard as the index view does, with no campaign yet."""
        return render_to_string(
            "stewardship/home.html",
            context
            | {"admin_chrome": admin}
            | {
                "configuration": SimpleNamespace(mode="testing"),
                "dashboard": {
                    "campaign": None,
                    "refreshed_at": None,
                    "security_events": rows,
                },
            },
        )

    return {
        "/dashboard-events": ("text/html", page(events)),
        "/dashboard-clear": ("text/html", page([])),
    }
