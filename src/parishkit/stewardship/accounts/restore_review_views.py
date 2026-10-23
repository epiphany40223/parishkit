"""The Restore review page (#537): what was restored, held emails, release.

After the operator's ``restore-begin`` command the site is closed: the access
gate sends an Administrator here (``/admin/maintenance``) and shows everyone
else a plain "the site is being restored" notice. The page says what was
restored, lists the emails that may already have gone out (held, grouped by
send), lets the Administrator settle each group, and releases the site.

Every step posts to this page, which answers with the page itself so
ui-v1.js swaps the review region in place; release answers with a redirect
to Home, which ends the review. The SQL guards check again that the
Administrator's session is live. No step shows or changes a Family code or
link.

After release, holds nobody decided stay in force. The Held emails page
(``held_emails``, ``/admin/mail/held-emails/``, #757) lists every restore's
holds of the current campaign by send, with the same per-send decisions and
the same in-place answers; Home links to it while any hold is undecided.
"""

from uuid import UUID, uuid4

from django.db import DatabaseError, IntegrityError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.campaigns import restore_review as review
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.refusals import (
    refusal_text,
    refused_step,
    unexpected_fields,
)

from .access_gate import status_page
from .admin_editing import error_response
from .admin_editing import principal as admin_principal
from .authentication import runtime
from .role_refusal import signed_out_response
from .sessions import authenticated_admin, sign_in_instant

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
_REFUSALS = {
    "signed_out": 403,
    "changed": 409,
    "blocked": 409,
    "needs_holds": 409,
    "superseded": 409,
    "held_elsewhere": 409,
    "invalid": 400,
}
# The Held emails page offers only the per-send decisions; its confirm also
# carries the latest restore the preview was read under.
_HELD_FIELDS = {
    "group-preview": _FIELDS["group-preview"],
    "group-confirm": _FIELDS["group-confirm"] | {"restore"},
    "cancel": _FIELDS["cancel"],
}


def _signed_out(error):
    """The guard's 42501 is a session that ended between check and write."""
    return getattr(error.__cause__, "sqlstate", None) == "42501"


@require_http_methods(["GET", "HEAD", "POST"])
def restore_review(request):
    """Show the review, or run one posted step and show it again in place."""
    service = runtime()
    principal = authenticated_admin(
        request, store=service.store, activity=request.method == "POST"
    )
    if principal is None:
        # A session that ended after the access gate's own check: the
        # gate's answer, the shared signed-out refusal (#2062).
        return signed_out_response()
    if "administrator" not in principal.roles:
        # Staff and Ministry leaders: the plain notice, never the review.
        return status_page(request, kind="maintenance", admin=True)
    state = review.review_state()
    if state is None:
        # Not (or no longer) under review: there is nothing to settle here.
        return redirect("admin:index")
    step, status = {}, 200
    if request.method == "POST":
        result = _answer(lambda: _step(request, principal, state))
        if result.get("released"):
            return redirect("admin:index")
        # The outcome is drawn after the controls of the panel that was used
        # (held emails, or release), so it never moves them.
        result["panel"] = (
            "release"
            if request.POST.get("action", "").startswith("release")
            else "emails"
        )
        step = result
        status = _REFUSALS.get(step.get("refused"), 200)
        state = review.review_state()
        if state is None:
            return redirect("admin:index")
    # Release is offered, unavailable with its reason, until no email still
    # needs a hold and no purge or campaign change runs: the reasons
    # ``release_refusal`` names, read now as the release itself reads them
    # (the SQL refuses a release before that too, #1331).
    refusal = "needs_holds" if state.needed else review.release_refusal()
    response = render(
        request,
        "stewardship/restore-review.html",
        {
            "review": state,
            "step": step,
            "release_ready": not refusal,
            "release_refusal": refusal,
            "page_url": reverse("admin:maintenance"),
        },
        status=status,
    )
    if status != 200:
        # The answer carries its own explanation; keep it as rendered.
        response.stewardship_safe_error = True
    if "refused" in step:
        # The refusal log records why the step was refused (#1403).
        refused_step(response, step["refused"], step.get("_error"))
    response["Cache-Control"] = "no-store"
    return response


def _answer(step):
    """Run one posted step; returns what the region shows, or the refusal.

    Shared by the Restore review and Held emails pages: each refusal a
    person can act on is named, so the page answers it in place.
    """
    try:
        return step()
    except StaleRecordError:
        return {"refused": "changed"}
    except review.ReleaseRefused as error:
        # Checked in Python under the guard's locks, so named here.
        return {"refused": error.reason}
    except review.ResendBlocked as error:
        return {"refused": error.reason}
    except IntegrityError as error:
        # The guards answer every other refusal with one SQLSTATE (23514),
        # so the page never reads their wording: the reasons a person can act
        # on (emails still needing a hold, a purge or campaign change
        # running, a refused resend) are checked first in Python and named
        # above. What reaches here is a session that ended between check
        # and write (42501), or a change that landed in between (another
        # step, more emails to hold), which the refreshed page then shows.
        if _signed_out(error):
            return {"refused": "signed_out"}
        return {"refused": "changed"}
    except DatabaseError as error:
        if not _signed_out(error):
            raise
        return {"refused": "signed_out"}
    except (ValueError, TypeError, KeyError) as error:
        # A stale form's unexpected fields say so in the region (#1401).
        # ``_error`` is for the refusal log only: a template cannot read it.
        return {
            "refused": "invalid",
            "refusal_text": refusal_text(error),
            "_error": error,
        }


def _step(request, principal, state):
    """Run one posted step; returns what the region shows next."""
    action = request.POST.get("action", "")
    if action not in _FIELDS:
        # No sources: an unknown action value, not an unexpected field.
        raise unexpected_fields()
    allowed = _FIELDS[action] | {"csrfmiddlewaretoken"}
    if set(request.POST) - allowed:
        raise unexpected_fields(request.POST, allowed=allowed)
    actor = principal.identity
    if action == "cancel":
        return {}
    if action == "list":
        added = review.list_held_emails(actor_id=actor, correlation_id=uuid4())
        return {"listed": added}
    # Every other step settles or releases, recording the session's
    # sign-in instant, which the guards bind to the live session.
    signed_in = sign_in_instant(request)
    session = request.portal_session
    if action in {"group-preview", "group-confirm"}:
        return _group_step(request, state.groups, actor, signed_in)
    if action == "release-preview":
        # The page offers no release while an email still needs a hold; a
        # posted preview is checked the same way, as release itself is.
        if reason := review.release_refusal():
            raise review.ReleaseRefused(reason)
        return {"release": True, "key": uuid4()}
    # The same named reasons before the release itself; one that arises
    # between this read and the guard's own check is answered as "changed".
    if reason := review.release_refusal():
        raise review.ReleaseRefused(reason)
    review.release_review(
        request_id=UUID(request.POST["key"]),
        expected_runtime_version=int(request.POST["version"]),
        actor_id=actor,
        session_id=session.pk,
        authenticated_at=signed_in,
        correlation_id=uuid4(),
    )
    return {"released": True}


def _group_step(request, groups, actor, signed_in, *, after_release=False):
    """Preview or confirm one send's decision (both pages); the region's answer.

    ``groups`` are the sends the page just read. A preview names the exact
    count, or refuses a resend the page shows unavailable; a confirm settles
    exactly that count, or nothing. After release the confirm also names the
    latest restore the preview was read under (empty when there was none),
    so a restore since then settles nothing even if the count still matches.
    """
    group = _group(groups, request.POST["definition"])
    group_action = request.POST["group_action"]
    if request.POST["action"] == "group-preview":
        if group_action not in review.GROUP_ACTIONS or not group.count(group_action):
            raise ValueError("Nothing to settle.")
        if group_action == "resend" and group.resend_blocked:
            # The page shows this choice unavailable, with its reason.
            raise review.RESEND_BLOCKS[group.resend_blocked](group.resend_blocked)
        return {"group": group, "group_action": group_action}
    restore = request.POST["restore"] if after_release else ""
    settled = review.settle_group(
        definition_id=group.definition_id,
        action=group_action,
        expected_count=int(request.POST["count"]),
        evidence=request.POST.get("evidence", ""),
        actor_id=actor,
        session_id=request.portal_session.pk,
        authenticated_at=signed_in,
        correlation_id=uuid4(),
        after_release=after_release,
        expected_restore_id=UUID(restore) if restore else None,
    )
    return {"settled": settled, "group_action": group_action}


def _group(groups, value):
    """The held-email group the form names, from the groups just read."""
    definition = UUID(value)
    for group in groups:
        if group.definition_id == definition:
            return group
    raise ValueError("Unknown send.")


@require_http_methods(["GET", "HEAD", "POST"])
def held_emails(request):
    """The Held emails page (#757): decide holds left undecided at release.

    Administrators only (CONFIGURE, as every Mail page). During a review the
    access gate already sends an Administrator to the Restore review page; a
    review begun while this page was open redirects there too. Each step
    posts here and is answered with the page, swapped in place.
    """
    try:
        service = runtime()
        actor = admin_principal(request, service)
    except (PermissionError, ValueError) as error:
        return error_response(error)
    state = review.held_emails()
    if state is None:
        return redirect("admin:maintenance")
    step, status = {}, 200
    if request.method == "POST":
        step = _answer(lambda: _held_step(request, actor, state))
        status = _REFUSALS.get(step.get("refused"), 200)
        state = review.held_emails()
        if state is None:
            return redirect("admin:maintenance")
    response = render(
        request,
        "stewardship/held-emails.html",
        {
            "held": state,
            "step": step,
            "page_url": reverse("admin:held_emails"),
        },
        status=status,
    )
    if status != 200:
        # The answer carries its own explanation; keep it as rendered.
        response.stewardship_safe_error = True
    if "refused" in step:
        # The refusal log records why the step was refused (#1403).
        refused_step(response, step["refused"], step.get("_error"))
    response["Cache-Control"] = "no-store"
    return response


def _held_step(request, actor, state):
    """Run one posted Held emails step; returns what the region shows next."""
    action = request.POST.get("action", "")
    if action not in _HELD_FIELDS:
        # No sources: an unknown action value, not an unexpected field.
        raise unexpected_fields()
    allowed = _HELD_FIELDS[action] | {"csrfmiddlewaretoken"}
    if set(request.POST) - allowed:
        raise unexpected_fields(request.POST, allowed=allowed)
    if action == "cancel":
        return {}
    # A decision records the session's sign-in instant, as above.
    signed_in = sign_in_instant(request)
    return _group_step(
        request, state.groups, actor.identity, signed_in, after_release=True
    )
