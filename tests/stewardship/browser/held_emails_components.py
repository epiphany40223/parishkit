"""Render the real Held emails template with sample states (#757).

The page is served at its real address, so the answers to its POSTs (at the
same address in the real app) are swapped in place by ui-v1.js; the browser
test routes each POST, by its action, to one of these answers.
"""

from datetime import timedelta
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.campaigns.restore_review import HeldEmails, HeldGroup

PAGE = "/admin/mail/held-emails/"
PREVIEW = "/held-emails-preview"
SETTLED = "/held-emails-settled"
STEP_UP = "/held-emails-step-up"
CHANGED = "/held-emails-changed"
# "Send these again" unavailable on each send, with its reason.
BLOCKED = "/held-emails-blocked"
# No restore held anything for the current campaign.
NONE = "/held-emails-none"
# Without a recent sign-in: each decision is unavailable, named by the
# step-up notice (#1331).
STALE = "/held-emails-stale"

INVITATION = UUID(int=757)
REMINDER = UUID(int=758)


def components(context, admin):
    """The page, and each answer its steps get."""
    now = context["server_now"]
    held = (
        # Two of the invitations are for Families that cannot be emailed now.
        HeldGroup(
            INVITATION, "initial", now - timedelta(days=4), unreviewed=12, reachable=10
        ),
        HeldGroup(
            REMINDER, "reminder", now - timedelta(hours=3), unreviewed=40, reachable=30
        ),
    )
    settled = (
        HeldGroup(INVITATION, "initial", now - timedelta(days=4), assumed=12),
        held[1],
    )
    blocked = (
        HeldGroup(
            INVITATION,
            "initial",
            now - timedelta(days=4),
            unreviewed=2,
            reachable=2,
            resend_blocked="held_elsewhere",
        ),
        HeldGroup(
            REMINDER, "reminder", None, unreviewed=1, resend_blocked="superseded"
        ),
    )
    chrome = admin | {"sections": [], "breadcrumbs": []}

    def page(groups, blocked_reminders=0, fresh=True, **step):
        """The page as the view renders it after one step; ``fresh`` says the
        sign-in is recent."""
        values = {
            "held": HeldEmails(groups=groups, reminders_blocked=blocked_reminders),
            "step": step,
            "fresh": fresh,
            "page_url": PAGE,
        }
        return (
            "text/html",
            render_to_string(
                "stewardship/held-emails.html",
                context | {"admin_chrome": chrome} | values,
            ),
        )

    return {
        PAGE: page(held, 10),
        PREVIEW: page(held, 10, group=held[0], group_action="assume"),
        SETTLED: page(settled, settled=12),
        STEP_UP: page(held, 10, fresh=False, refused="reauthenticate"),
        STALE: page(held, 10, fresh=False),
        CHANGED: page(held, 10, refused="changed"),
        BLOCKED: page(blocked, 2),
        NONE: page(()),
    }
