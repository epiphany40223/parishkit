"""Admin automation pages for the browser tests, from the real templates (ADM-11).

Automation access and the dashboard are served at their real addresses,
because a form[data-in-place] swaps regions only when its post redirects
back to the same page (#559): the page before a change at its address, and
the page after it at the same address with a query (the fixture server's
POST answers redirect there). The approval page is served in each of its
states: a stale sign-in, code entry, the review and the approved result.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

NOW = datetime(2026, 10, 4, 15, tzinfo=UTC)
ACCESS = reverse("admin:automation_access")
APPROVAL = reverse("admin:automation_approval")
NOTICES = reverse("admin:automation_notices")
HOME = reverse("admin:index")
OWN = UUID(int=701)
OTHER = UUID(int=702)
REVOKE = reverse("admin:automation_session", args=[OWN])
# The fixture server's answers to the pages' posts (status, Location, body).
POSTS = {
    REVOKE: (303, ACCESS + "?revoked=1", ""),
    NOTICES: (303, HOME + "?cleared=1", ""),
}


def _session(identifier, label, *, live=True, reason=None, email=None):
    """One session row as the views shape it."""
    return {
        "id": identifier,
        "label": label,
        "scope": "full",
        "scope_label": "Full",
        "created_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=29),
        "last_used_at": NOW - timedelta(hours=1),
        "revoked_at": None if live else NOW,
        "end_reason": reason,
        "reason_label": "Revoked by you" if reason else None,
        "host": "0123456789ab",
        "live": live,
        "principal_email": email,
        "own": email == "admin@example.org",
    }


def components(context, admin):
    """The pages a browser test opens, keyed by exact path."""
    responses = {}

    def access(own_live, *, fresh=True):
        """Automation access before or after revoking the own session."""
        own = _session(
            OWN,
            "launch <assistant>",
            live=own_live,
            reason=None if own_live else "revoked_by_owner",
        )
        live = [_session(OTHER, "other job", email="other@example.org")]
        if own_live:
            live.insert(0, own | {"principal_email": "admin@example.org", "own": True})
        return render_to_string(
            "stewardship/automation-access.html",
            context
            | {"admin_chrome": admin}
            | {
                "sessions": [own],
                "live": live,
                "fresh": fresh,
                "approval_url": APPROVAL,
                "filler": range(30),
            },
        )

    responses[ACCESS] = ("text/html", access(True))
    responses[ACCESS + "?revoked=1"] = ("text/html", access(False))
    responses["/automation-access-stale"] = ("text/html", access(True, fresh=False))

    def home(rows):
        """The dashboard with the automation notices region (empty or not)."""
        return render_to_string(
            "stewardship/home.html",
            context
            | {"admin_chrome": admin}
            | {
                "configuration": SimpleNamespace(mode="testing"),
                "dashboard": {
                    "campaign": None,
                    "refreshed_at": None,
                    "automation_notices": {"total": len(rows), "rows": rows},
                },
            },
        )

    notice = {
        "id": UUID(int=703),
        "kind": "approved",
        "label": "launch <assistant>",
        "command_type": None,
        "created_at": NOW,
    }
    refused = notice | {"id": UUID(int=704), "kind": "refused", "label": None}
    responses[HOME] = ("text/html", home([notice]))
    responses[HOME + "?two=1"] = ("text/html", home([notice, refused]))
    responses[HOME + "?cleared=1"] = ("text/html", home([]))

    def approval(**extra):
        """The approval page in one state."""
        return render_to_string(
            "stewardship/automation-approval.html",
            context
            | {"admin_chrome": admin}
            | {
                "back": ACCESS,
                "back_label": "Back to Automation access",
                "next": APPROVAL,
            }
            | extra,
        )

    pending = {
        "label": "launch <assistant>",
        "expect_email": "admin@example.org",
        "scope": "full",
        "days": 30,
        "host_digest": "0123456789ab" + "c" * 52,
    }
    responses[APPROVAL] = ("text/html", approval(state="enter", fresh=True))
    responses["/automation-approval-stale"] = (
        "text/html",
        approval(state="enter", fresh=False),
    )
    responses["/automation-approval-review"] = (
        "text/html",
        approval(
            state="review",
            fresh=True,
            code="ABCD-EFGH",
            pending=pending,
            scope_label="Full",
            host="0123456789ab",
            scope_choices=[("full", "Full"), ("read_only", "Read-only")],
        ),
    )
    return responses
