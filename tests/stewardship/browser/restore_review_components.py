"""Render the real Restore review template with sample states (#537).

The page is served at its real address, so the answers to its POSTs (at the
same address in the real app) are swapped in place by ui-v1.js; the browser
test routes each POST, by its action, to one of these answers.
"""

from datetime import timedelta
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.campaigns.restore_review import HeldGroup, ReviewState

PAGE = "/admin/maintenance"
LISTED = "/restore-review-listed"
GROUP_PREVIEW = "/restore-review-group-preview"
SETTLED = "/restore-review-settled"
RELEASE_PREVIEW = "/restore-review-release-preview"
STEP_UP = "/restore-review-step-up"
CHANGED = "/restore-review-changed"
INERT_PREVIEW = "/restore-review-inert-preview"
# The page before anything was found, in Testing (nothing is ever held).
TESTING = "/restore-review-testing"

INVITATION = UUID(int=537)
REMINDER = UUID(int=538)


def review(now, *, groups=(), mode="production", needed=0):
    """A review of a backup taken 14 hours before the restore."""
    return ReviewState(
        restore_id=UUID(int=1),
        runtime_version=12,
        backup_at=now - timedelta(hours=15),
        restored_at=now - timedelta(hours=1),
        mode=mode,
        campaign_name="2054 Stewardship",
        campaign_state="active",
        listed=bool(groups),
        groups=tuple(groups),
        in_flight=2 if groups else 0,
        needed=needed,
    )


def components(context, admin):
    """The page, and each answer its steps get."""
    now = context["server_now"]
    found = (
        # Two of the invitations are for Families that cannot be emailed now.
        HeldGroup(
            INVITATION, "initial", now - timedelta(days=4), unreviewed=12, reachable=10
        ),
        HeldGroup(
            REMINDER, "reminder", now - timedelta(hours=3), unreviewed=40, reachable=40
        ),
    )
    settled = (
        HeldGroup(INVITATION, "initial", now - timedelta(days=4), assumed=12),
        found[1],
    )
    inert = (HeldGroup(INVITATION, "initial", now - timedelta(days=4), unreviewed=3),)
    chrome = admin | {"sections": [], "breadcrumbs": [], "restored": True}

    def page(state, **step):
        """The page as the view renders it after one step."""
        return (
            "text/html",
            render_to_string(
                "stewardship/restore-review.html",
                context | {"admin_chrome": chrome, "review": state, "step": step},
            ),
        )

    return {
        # Before anything is found: 52 emails still need a hold.
        PAGE: page(review(now, needed=52)),
        TESTING: page(review(now, mode="testing")),
        LISTED: page(review(now, groups=found), listed=52),
        GROUP_PREVIEW: page(
            review(now, groups=found), group=found[0], group_action="assume"
        ),
        # Every undecided hold of this send would send nothing now.
        INERT_PREVIEW: page(
            review(now, groups=inert), group=inert[0], group_action="resend"
        ),
        SETTLED: page(review(now, groups=settled), settled=12),
        RELEASE_PREVIEW: page(
            review(now, groups=settled), release=True, key=UUID(int=9)
        ),
        STEP_UP: page(review(now, groups=found), refused="reauthenticate"),
        CHANGED: page(review(now, groups=found), refused="changed"),
    }
