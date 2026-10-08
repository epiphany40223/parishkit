"""The Held emails page (#757): settle restore holds after release.

A restore review can be released with held emails nobody decided on. Each
such hold keeps its own email back for good, and an undecided invitation also
keeps back every reminder of its Family. This page, for an Administrator,
lists the remaining undecided holds of the current campaign by send (from any
restore) and offers the same two decisions per send as the Restore review
page: assume these were sent, or send these again.

Every step posts to this page, which answers with the page itself so
ui-v1.js swaps the region in place. A decision needs a Google sign-in within
five minutes: a stale one is answered in place with the "Confirm with Google"
step-up, and the SQL guard checks it again. No step shows or changes a Family
code or link; a resend is sent later by the ordinary planner.
"""

from uuid import UUID, uuid4

from django.db import DatabaseError, IntegrityError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.campaigns import restore_review as review
from parishkit.stewardship.storage import StaleRecordError

from .admin_editing import error_response
from .authentication import runtime
from .sessions import FreshAuthenticationRequired, authenticated_admin, require_fresh

# The form fields each step accepts; anything else is refused unread.
_FIELDS = {
    "group-preview": {"action", "definition", "group_action"},
    "group-confirm": {"action", "definition", "group_action", "count", "evidence"},
    "cancel": {"action"},
}
# An in-place refusal's HTTP status, by the reason the region states.
_REFUSALS = {"reauthenticate": 403, "changed": 409, "invalid": 400}


def _groups(configuration):
    """The sends that still have an undecided hold, and the blocked count."""
    groups, blocked = review.held_groups(configuration)
    return tuple(group for group in groups if group.unreviewed), blocked


@require_http_methods(["GET", "HEAD", "POST"])
def held_emails(request):
    """Show the held emails, or run one posted step and show them again."""
    service = runtime()
    principal = authenticated_admin(
        request, store=service.store, activity=request.method == "POST"
    )
    if principal is None:
        return redirect("admin:login")
    if "administrator" not in principal.roles:
        # The closed "access unavailable" answer, as other Admin-only pages.
        return error_response(PermissionError("Held emails are for an Administrator."))
    configuration = SystemConfiguration.objects.get()
    if configuration.restore_review_required:
        # Under review the Restore review page owns these decisions.
        return redirect("admin:maintenance")
    step, status = {}, 200
    if request.method == "POST":
        groups, _ = _groups(configuration)
        try:
            step = _step(request, principal, groups)
        except FreshAuthenticationRequired:
            step = {"refused": "reauthenticate"}
        except (StaleRecordError, IntegrityError):
            # A changed count, or another decision on the same hold that won
            # the race (the guard's 23514).
            step = {"refused": "changed"}
        except DatabaseError as error:
            # The guard's fresh-sign-in refusal, when the sign-in lapsed
            # between the page's check and the write.
            if not review.sign_in_lapsed(error):
                raise
            step = {"refused": "reauthenticate"}
        except (ValueError, TypeError, KeyError):
            step = {"refused": "invalid"}
        status = _REFUSALS.get(step.get("refused"), 200)
    groups, blocked = _groups(configuration)
    response = render(
        request,
        "stewardship/held-emails.html",
        {
            "groups": groups,
            "reminders_blocked": blocked,
            "step": step,
            # The region states its own count, which an in-place settle
            # updates; the every-page banner would go stale here.
            "hide_held_banner": True,
        },
        status=status,
    )
    if status != 200:
        # The answer carries its own explanation; keep it as rendered.
        response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    return response


def _step(request, principal, groups):
    """Run one posted step; returns what the region shows next."""
    action = request.POST.get("action", "")
    if action not in _FIELDS or set(request.POST) - _FIELDS[action] - {
        "csrfmiddlewaretoken"
    }:
        raise ValueError("Invalid held-email step.")
    if action == "cancel":
        return {}
    # Both steps settle: the step-up comes before the Administrator writes
    # a note.
    signed_in = require_fresh(request)
    group = _group(groups, request.POST["definition"])
    group_action = request.POST["group_action"]
    if group_action not in review.GROUP_ACTIONS:
        raise ValueError("Unknown held-email action.")
    if action == "group-preview":
        return {"group": group, "group_action": group_action}
    settled = review.settle_group(
        definition_id=group.definition_id,
        action=group_action,
        expected_count=int(request.POST["count"]),
        evidence=request.POST.get("evidence", ""),
        actor_id=principal.identity,
        session_id=request.portal_session.pk,
        authenticated_at=signed_in,
        correlation_id=uuid4(),
    )
    return {
        "settled": settled.count,
        "converted": settled.assumed_instead,
        "group_action": group_action,
    }


def _group(groups, value):
    """The send the form names, from the holds just read."""
    definition = UUID(value)
    for group in groups:
        if group.definition_id == definition:
            return group
    # Settled by someone else since the page was shown.
    raise StaleRecordError("This send has no undecided held email now.")
