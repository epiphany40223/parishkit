"""Render the real Held emails template with sample states (#757).

The page is served at its real address, so the answers to its POSTs (at the
same address in the real app) are swapped in place by ui-v1.js; the browser
test routes each POST, by its action, to one of these answers.
"""

from datetime import timedelta
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.campaigns.restore_review import HeldGroup

PAGE = "/admin/deliveries/held-emails"
PREVIEW = "/held-emails-preview"
SETTLED = "/held-emails-settled"
STEP_UP = "/held-emails-step-up"
NONE = "/held-emails-none"

INVITATION = UUID(int=757)


def components(context, admin):
    """The page, and each answer its steps get."""
    now = context["server_now"]
    held = (
        HeldGroup(
            INVITATION, "initial", now - timedelta(days=4), unreviewed=12, reachable=9
        ),
    )
    chrome = admin | {"sections": [], "breadcrumbs": [], "held_invitations": 9}

    def page(groups, blocked=0, **step):
        """The page as the view renders it after one step."""
        return (
            "text/html",
            render_to_string(
                "stewardship/held-emails.html",
                context
                | {
                    "admin_chrome": chrome,
                    "groups": groups,
                    "reminders_blocked": blocked,
                    "step": step,
                },
            ),
        )

    return {
        PAGE: page(held, 9),
        PREVIEW: page(held, 9, group=held[0], group_action="resend"),
        SETTLED: page((), 0, settled=12),
        STEP_UP: page(held, 9, refused="reauthenticate"),
        NONE: page(()),
    }
