"""The browser side of Admin automation: the approval page (ADM-11).

The page the command line's ``login start`` links to, under Users and
access at ``/admin/users/automation/approval/`` (see the Admin automation
specification): an Administrator enters the user code, reviews the pending
session and approves it.

Approval needs the Administrator role and a Google sign-in within five
minutes. A stale sign-in never gets a refusal: the page renders its
"Confirm with Google" step and keeps Continue and Approve unavailable until
the sign-in is fresh, as the backup key page does. The user code has no
attempt limit (Administrator decision 12): a wrong code, and a code meant for
another Administrator's address, get the same reply, which names nothing
about any pairing. The approval page follows the Admin confirmation guidance
(#523): one plain sentence, the result shown in place, and a link back.
"""

from django.core import signing
from django.db import DatabaseError, transaction
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods
from redis.exceptions import RedisError

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import error_response, principal
from .authentication import runtime
from .automation_sessions import (
    PairingRefused,
    PairingStore,
    approve,
    display_code,
    normalized_code,
)
from .limiting import LimiterUnavailable
from .policy import Capability
from .policy_models import PortalUser
from .policy_schema import normalized_email
from .sessions import FreshAuthenticationRequired, require_fresh

REFUSALS = (
    ConfigError,
    DatabaseError,
    LimiterUnavailable,
    LookupError,
    PermissionError,
    StaleRecordError,
    ValueError,
    signing.BadSignature,
)

SCOPES = {"full": _("Full"), "read_only": _("Read-only")}


def _fields(parameters, allowed):
    """Refuse unexpected or repeated form fields before reading any of them."""
    if set(parameters) - allowed or any(
        len(values) != 1 for _, values in parameters.lists()
    ):
        raise ValueError("Invalid automation form fields.")


def _administrator(request, service):
    """Admit the signed-in Administrator; these pages are theirs alone."""
    actor = principal(request, service, capability=Capability.MANAGE_USERS)
    if "administrator" not in actor.roles:
        raise PermissionError("Automation sessions are for Administrators.")
    return actor


def _fresh(request):
    """Whether this browser signed in with Google within five minutes."""
    try:
        require_fresh(request)
    except FreshAuthenticationRequired:
        return False
    return True


def _expected(actor, pending):
    """Whether the pending pairing names this Administrator's own address."""
    email = PortalUser.objects.values_list("email", flat=True).get(pk=actor.identity)
    return normalized_email(email) == pending["expect_email"]


def _approval_page(request, context):
    """Render the approval page; it is never cached."""
    context.setdefault("back", reverse("admin:index"))
    context.setdefault("back_label", _("Back to Home"))
    context.setdefault("next", reverse("admin:automation_approval"))
    response = render(request, "stewardship/automation-approval.html", context)
    response["Cache-Control"] = "no-store"
    return response


@require_http_methods(["GET", "HEAD", "POST"])
def approval_view(request):
    """Enter a user code, review the pending session, and approve it in place.

    ``lookup`` shows the request: its label (escaped as text), the expected
    address, the requested scope as plain text, the first 12 characters of
    the requesting host's digest, and the requested lifetime, which the form
    lets the Administrator shorten; the scope can only be lowered. ``approve``
    creates the session and shows the result on this page. Without a fresh
    sign-in the page offers "Confirm with Google" and accepts no code.
    """
    try:
        service = runtime()
        actor = _administrator(request, service)
        filters(request.GET, allowed=set())
        if not _fresh(request):
            return _approval_page(request, {"state": "enter", "fresh": False})
        context = {"state": "enter", "fresh": True}
        if request.method == "POST":
            action = request.POST.get("action")
            allowed = {
                "lookup": {"action", "csrfmiddlewaretoken", "code"},
                "approve": {"action", "csrfmiddlewaretoken", "code", "scope", "days"},
            }.get(action)
            if allowed is None:
                raise ValueError("Unknown automation action.")
            _fields(request.POST, allowed)
            code = normalized_code(request.POST.get("code"))
            store = PairingStore(service.limiter.client, service.limiter.namespace)
            try:
                pending = None if code is None else store.request(code)
            except RedisError:
                raise LimiterUnavailable() from None
            if pending is None or not _expected(actor, pending):
                # The same reply for an unknown code and another's pairing.
                context["refused"] = True
            elif action == "lookup":
                context.update(
                    state="review",
                    code=display_code(code),
                    pending=pending,
                    scope_label=SCOPES[pending["scope"]],
                    host=pending["host_digest"][:12],
                    scope_choices=[
                        (value, SCOPES[value])
                        for value in ("full", "read_only")
                        if pending["scope"] == "full" or value == "read_only"
                    ],
                )
            else:
                try:
                    days = int(request.POST.get("days", ""))
                except ValueError:
                    raise ValueError("The lifetime is a number of days.") from None
                with transaction.atomic():
                    session = approve(
                        actor,
                        request.portal_session,
                        pending,
                        scope=request.POST.get("scope"),
                        days=days,
                    )
                context.update(
                    state="approved",
                    session=session,
                    scope_label=SCOPES[session.scope],
                )
        return _approval_page(request, context)
    except PairingRefused:
        context = {"state": "enter", "fresh": True, "refused": True}
        return _approval_page(request, context)
    except REFUSALS as error:
        return error_response(error)
