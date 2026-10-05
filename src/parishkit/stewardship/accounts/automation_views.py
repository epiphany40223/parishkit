"""The browser side of Admin automation: approval, access and notices (ADM-11).

These pages are the human side of the command line (see the Admin automation
specification): an Administrator approves a pending pairing, lists their own
sessions and every live session, revokes any live one, and acknowledges
automation notices on the dashboard. They live under Users and access, at
``/admin/users/automation/`` (Automation access) and
``/admin/users/automation/approval/`` (the approval page), with their POST
actions on the session and notice collections beneath them.

Approval needs the Administrator role and a Google sign-in within five
minutes. A stale sign-in never gets a refusal: the page renders its
"Confirm with Google" step and keeps Continue and Approve unavailable until
the sign-in is fresh, as the backup key page does. The user code has no
attempt limit (Administrator decision 12): a wrong code, and a code meant for
another Administrator's address, get the same reply, which names nothing
about any pairing. The approval page follows the Admin confirmation guidance
(#523): one plain sentence, the result shown in place, and a link back.

Revoke and acknowledge are ``form[data-in-place]`` posts (#559): each
redirects back to its own page, and the page's regions are swapped in place.
"""

from uuid import UUID

from django.core import signing
from django.db import DatabaseError, transaction
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import (
    require_http_methods,
    require_POST,
    require_safe,
)
from redis.exceptions import RedisError

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import error_response, principal
from .authentication import runtime
from .automation_models import AutomationSession
from .automation_sessions import (
    PairingRefused,
    PairingStore,
    acknowledge_notices,
    approve,
    database_now,
    display_code,
    live_sessions,
    normalized_code,
    revoke,
    sessions_of,
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

# How each ending reads on the Automation access page.
END_REASONS = {
    "logout": _("Ended from the command line"),
    "revoked_by_owner": _("Revoked by you"),
    "revoked_by_administrator": _("Revoked by another Administrator"),
    "role_lost": _("Ended: you are no longer an Administrator"),
    "user_removed": _("Ended: your account was disabled or removed"),
    "recovery": _("Ended by an offline Admin-access recovery"),
    "restore": _("Ended when the database was restored"),
    "revoked_by_operator": _("Ended by the server operator"),
    "host_mismatch": _("Ended: used from a different server"),
    "misused": _("Ended: its key was used outside the command line"),
    "pairing_abandoned": _("Ended: approved, but never collected by the command line"),
}
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
    context.setdefault("back", reverse("admin:automation_access"))
    context.setdefault("back_label", _("Back to Automation access"))
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


@require_safe
def access_view(request):
    """This Administrator's sessions, and every live session of any Administrator.

    The own list shows sessions live and ended in the last 30 days; the
    second list shows every live session, which any Administrator may revoke.
    Approving a new session first asks for a fresh Google sign-in.
    """
    try:
        service = runtime()
        actor = _administrator(request, service)
        filters(request.GET, allowed=set())
        now = database_now()
        own = sessions_of(actor.identity, now)
        for row in own:
            row["scope_label"] = SCOPES[row["scope"]]
            row["reason_label"] = END_REASONS.get(row["end_reason"])
        everyone = live_sessions()
        for row in everyone:
            row["scope_label"] = SCOPES[row["scope"]]
            row["own"] = row["principal_id"] == actor.identity
        response = render(
            request,
            "stewardship/automation-access.html",
            {
                "sessions": own,
                "live": everyone,
                "fresh": _fresh(request),
                "approval_url": reverse("admin:automation_approval"),
            },
        )
        response["Cache-Control"] = "no-store"
        return response
    except REFUSALS as error:
        return error_response(error)


@require_POST
def session_view(request, session_id):
    """Revoke one live session, then return to Automation access.

    Revoking one's own session records ``revoked_by_owner``; any other
    Administrator's, ``revoked_by_administrator``. It takes effect at that
    session's next command.
    """
    try:
        service = runtime()
        actor = _administrator(request, service)
        filters(request.GET, allowed=set())
        _fields(request.POST, {"csrfmiddlewaretoken"})
        session = AutomationSession.objects.filter(pk=session_id).first()
        if session is None:
            raise LookupError("Automation session is unavailable.")
        own = session.principal_id == actor.identity
        revoke(
            session_id,
            actor,
            reason="revoked_by_owner" if own else "revoked_by_administrator",
        )
        return HttpResponseRedirect(
            reverse("admin:automation_access") + "#automation-sessions"
        )
    except REFUSALS as error:
        return error_response(error)


@require_POST
def notices_view(request):
    """Acknowledge one notice, or all of them, on this Administrator's dashboard."""
    try:
        service = runtime()
        actor = _administrator(request, service)
        filters(request.GET, allowed=set())
        _fields(request.POST, {"csrfmiddlewaretoken", "notice"})
        value = request.POST.get("notice")
        notices = None if value in (None, "all") else [UUID(value)]
        with transaction.atomic():
            acknowledge_notices(actor, notices)
        return HttpResponseRedirect(reverse("admin:index") + "#automation-notices")
    except REFUSALS as error:
        return error_response(error)
