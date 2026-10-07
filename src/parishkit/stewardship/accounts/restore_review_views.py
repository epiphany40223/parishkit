"""The Restore review page (#537): what was restored, held emails, release.

After the operator's ``restore-begin`` command the site is closed: the access
gate sends an Administrator here (``/admin/maintenance``) and shows everyone
else a plain "the site is being restored" notice. The page says what was
restored, lists the emails that may already have gone out (held, grouped by
send), lets the Administrator settle each group, and releases the site.

Every step posts to this page, which answers with the page itself so
ui-v1.js swaps the review region in place; release answers with a redirect
to Home, which ends the review. Settling and releasing need a Google sign-in
within five minutes: a stale one is answered in place with the "Confirm with
Google" step-up, and the SQL guards check it again. No step shows or changes
a Family code or link.
"""

from uuid import UUID, uuid4

from django.db import DatabaseError, IntegrityError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.campaigns import restore_review as review
from parishkit.stewardship.storage import StaleRecordError

from .access_gate import status_page
from .authentication import runtime
from .sessions import (
    FreshAuthenticationRequired,
    authenticated_admin,
    require_fresh,
)

# The form fields each step accepts; anything else is refused unread.
_FIELDS = {
    "list": {"action"},
    "group-preview": {"action", "definition", "group_action"},
    "group-confirm": {"action", "definition", "group_action", "count", "evidence"},
    "release-preview": {"action"},
    "release-confirm": {"action", "key", "version"},
    "cancel": {"action"},
}
# An in-place refusal's HTTP status, by the reason the region states.
_REFUSALS = {"reauthenticate": 403, "changed": 409, "blocked": 409, "invalid": 400}


def _fresh_or_refused(error):
    """The guard's 42501 is a sign-in that lapsed between check and write."""
    return getattr(error.__cause__, "sqlstate", None) == "42501"


@require_http_methods(["GET", "HEAD", "POST"])
def restore_review(request):
    """Show the review, or run one posted step and show it again in place."""
    service = runtime()
    principal = authenticated_admin(
        request, store=service.store, activity=request.method == "POST"
    )
    if principal is None:
        return redirect("admin:login")
    if "administrator" not in principal.roles:
        # Staff and Ministry leaders: the plain notice, never the review.
        return status_page(request, kind="maintenance", admin=True)
    state = review.review_state()
    if state is None:
        # Not (or no longer) under review: there is nothing to settle here.
        return redirect("admin:index")
    step, status = {}, 200
    if request.method == "POST":
        try:
            result = _step(request, principal, state)
        except FreshAuthenticationRequired:
            result = {"refused": "reauthenticate"}
        except StaleRecordError:
            result = {"refused": "changed"}
        except IntegrityError as error:
            # The release guard's refusal (a purge or campaign change still
            # running) or a fresh-sign-in lapse between check and write.
            if _fresh_or_refused(error):
                result = {"refused": "reauthenticate"}
            elif request.POST.get("action") == "release-confirm" and (
                "stale" not in str(error)
            ):
                # The release guard: a purge or campaign change still running.
                result = {"refused": "blocked"}
            else:
                # Another decision on the same hold won the race.
                result = {"refused": "changed"}
        except DatabaseError as error:
            if not _fresh_or_refused(error):
                raise
            result = {"refused": "reauthenticate"}
        except (ValueError, TypeError, KeyError):
            result = {"refused": "invalid"}
        if result.get("released"):
            return redirect("admin:index")
        step = result
        status = _REFUSALS.get(step.get("refused"), 200)
        state = review.review_state()
        if state is None:
            return redirect("admin:index")
    response = render(
        request,
        "stewardship/restore-review.html",
        {"review": state, "step": step},
        status=status,
    )
    if status != 200:
        # The answer carries its own explanation; keep it as rendered.
        response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    return response


def _step(request, principal, state):
    """Run one posted step; returns what the region shows next."""
    action = request.POST.get("action", "")
    if action not in _FIELDS or set(request.POST) - _FIELDS[action] - {
        "csrfmiddlewaretoken"
    }:
        raise ValueError("Invalid restore review step.")
    actor = principal.identity
    if action == "cancel":
        return {}
    if action == "list":
        added = review.list_held_emails(actor_id=actor, correlation_id=uuid4())
        return {"listed": added}
    # Every other step settles or releases: it needs a fresh sign-in first,
    # so the step-up comes before the Administrator writes a note.
    signed_in = require_fresh(request)
    session = request.portal_session
    if action == "group-preview":
        group = _group(state, request.POST["definition"])
        group_action = request.POST["group_action"]
        if group_action not in review.GROUP_ACTIONS or not group.count(group_action):
            raise ValueError("Nothing to settle.")
        return {"group": group, "group_action": group_action}
    if action == "group-confirm":
        group = _group(state, request.POST["definition"])
        settled = review.settle_group(
            definition_id=group.definition_id,
            action=request.POST["group_action"],
            expected_count=int(request.POST["count"]),
            evidence=request.POST.get("evidence", ""),
            actor_id=actor,
            session_id=session.pk,
            authenticated_at=signed_in,
            correlation_id=uuid4(),
        )
        return {"settled": settled, "group_action": request.POST["group_action"]}
    if action == "release-preview":
        # The page offers no release while an email still needs a hold; a
        # posted preview is checked the same way (the SQL refuses too).
        if review.holds_needed():
            raise StaleRecordError("Emails still need a hold.")
        return {"release": True, "key": uuid4()}
    review.release_review(
        request_id=UUID(request.POST["key"]),
        expected_runtime_version=int(request.POST["version"]),
        actor_id=actor,
        session_id=session.pk,
        authenticated_at=signed_in,
        correlation_id=uuid4(),
    )
    return {"released": True}


def _group(state, value):
    """The held-email group the form names, from the review just read."""
    definition = UUID(value)
    for group in state.groups:
        if group.definition_id == definition:
            return group
    raise ValueError("Unknown send.")
