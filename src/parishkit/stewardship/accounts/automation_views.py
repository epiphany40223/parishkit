"""The browser side of Admin automation: approval, access and notices (ADM-11).

These pages are the human side of the command line (see the Admin automation
specification): an Administrator approves a pending pairing, lists every live
session (and, on request, their own ended ones), revokes any live one, and
acknowledges automation notices on the dashboard. They live under Users and access, at
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

Acknowledge is a ``form[data-in-place]`` post (#559) that redirects back to
the dashboard, whose regions are swapped in place. Revoke is a row action
(#879): its icon button opens the shared confirmation dialog, which posts the
session to the collection and then redraws both session tables in place.
"""

from urllib.parse import urlencode
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
from parishkit.stewardship.web.tables import whole_table

from .admin_editing import error_response, principal
from .authentication import runtime
from .automation_models import AutomationSession
from .automation_sessions import (
    LIVE_SORTING,
    OWN_SORTING,
    PairingRefused,
    PairingStore,
    acknowledge_notices,
    approve,
    database_now,
    display_code,
    live_sessions,
    normalized_code,
    own_sessions,
    revoke,
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
    context.setdefault("back_label", _("Return to Automation access"))
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


# Automation access's only query parameters (#621): "ended=yes" shows this
# Administrator's ended sessions, and each table's sort is one of its own
# fixed tokens (``LIVE_SORTING`` and ``OWN_SORTING``). Anything else, an
# unknown token or another value of "ended" is refused.
LIVE = "live_"
ENDED = "ended_"
ACCESS_PARAMETERS = {"ended", f"{LIVE}sort", f"{ENDED}sort"}


def _access_state(parameters):
    """The validated page state: (include_ended, live sort, ended sort)."""
    values = filters(parameters, allowed=ACCESS_PARAMETERS)
    ended = values.get("ended")
    if ended not in (None, "yes"):
        raise ValueError("Unsupported automation session filter.")
    return (
        ended == "yes",
        LIVE_SORTING.parse(values, LIVE),
        OWN_SORTING.parse(values, ENDED),
    )


def _state_fields(include_ended, live_sort, ended_sort):
    """The page state as (name, value) pairs, defaults left out.

    Every heading, the filter form and the revoke form carry these, so no
    control resets another's choice and a default page keeps a bare URL.
    """
    return [
        (name, value)
        for name, value, default in (
            ("ended", "yes" if include_ended else "", ""),
            (f"{LIVE}sort", live_sort, LIVE_SORTING.default),
            (f"{ENDED}sort", ended_sort, OWN_SORTING.default),
        )
        if value != default
    ]


def _access_url(fields, anchor):
    """Automation access with this state, landing on the region ``anchor``."""
    query = urlencode(fields)
    return reverse("admin:automation_access") + (f"?{query}" if query else "") + anchor


def access_context(state, identity, everyone, ended):
    """Automation access's template context for one validated page state.

    ``state`` is ``_access_state``'s answer, ``everyone`` the live sessions
    of every Administrator and ``ended`` this Administrator's ended ones
    (empty unless asked for). Each table is sorted whole by its own token,
    and every heading carries the rest of the state, so changing one
    choice never resets another. Kept apart from the reads so the browser
    tests render the page exactly as the view does.
    """
    include_ended, live_sort, ended_sort = state
    fields = _state_fields(*state)
    for row in everyone:
        row["scope_label"] = SCOPES[row["scope"]]
        row["own"] = row["principal_id"] == identity
    for row in ended:
        row["scope_label"] = SCOPES[row["scope"]]
        row["reason_label"] = END_REASONS.get(row["end_reason"])
    return {
        "live_table": whole_table(
            everyone,
            sorting=LIVE_SORTING,
            sort=live_sort,
            prefix=LIVE,
            carry=[pair for pair in fields if pair[0] != f"{LIVE}sort"],
        ),
        "ended_table": whole_table(
            ended,
            sorting=OWN_SORTING,
            sort=ended_sort,
            prefix=ENDED,
            carry=[pair for pair in fields if pair[0] != f"{ENDED}sort"],
        ),
        "include_ended": include_ended,
        # The box's form keeps both sorts; the box itself gives "ended".
        "sort_fields": [pair for pair in fields if pair[0] != "ended"],
        # The revoke form's query, so the page it returns to is unchanged,
        # and the address the confirmation dialog redraws both tables from.
        "state_query": urlencode(fields),
        "refresh_url": _access_url(fields, ""),
        "approval_url": reverse("admin:automation_approval"),
    }


@require_safe
def access_view(request):
    """Every live session first; this Administrator's ended ones on request.

    The live table lists every live session of any Administrator, each with
    Revoke. Ticking "Include ended sessions" (``ended=yes``) adds a second
    table of this Administrator's sessions that ended in the last 30 days,
    never offered Revoke. Both tables sort by their headings (``live_sort``
    and ``ended_sort``), and every control refreshes the page in place.
    Approving a new session first asks for a fresh Google sign-in.
    """
    try:
        service = runtime()
        actor = _administrator(request, service)
        state = _access_state(request.GET)
        ended = []
        if state[0]:
            ended = [
                row
                for row in own_sessions(
                    actor.identity, database_now(), include_ended=True
                )
                if not row["live"]
            ]
        context = access_context(state, actor.identity, live_sessions(), ended)
        context["fresh"] = _fresh(request)
        response = render(request, "stewardship/automation-access.html", context)
        response["Cache-Control"] = "no-store"
        return response
    except REFUSALS as error:
        return error_response(error)


@require_POST
def sessions_view(request):
    """Revoke the one live session the confirmation dialog names (#879).

    The dialog on Automation access posts ``session_id`` here. Revoking one's
    own session records ``revoked_by_owner``; any other Administrator's,
    ``revoked_by_administrator``. It takes effect at that session's next
    command; one that has already ended is left as it is. The form's query
    carries the page's filter and sorts, validated as the page validates
    them, so the redirect returns to the reader's view; the dialog then
    redraws both tables in place.
    """
    try:
        service = runtime()
        actor = _administrator(request, service)
        state = _state_fields(*_access_state(request.GET))
        _fields(request.POST, {"csrfmiddlewaretoken", "session_id"})
        session_id = UUID(request.POST.get("session_id", ""))
        session = AutomationSession.objects.filter(pk=session_id).first()
        if session is None:
            raise LookupError("Automation session is unavailable.")
        own = session.principal_id == actor.identity
        revoke(
            session_id,
            actor,
            reason="revoked_by_owner" if own else "revoked_by_administrator",
        )
        return HttpResponseRedirect(_access_url(state, "#live-table"))
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
