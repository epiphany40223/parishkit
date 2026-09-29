"""The Administrator's switch that closes the Family portal for maintenance.

The switch has no table of its own: its state is the newest of two audit
events, ``family_maintenance_started`` and ``family_maintenance_ended``. Each
change is therefore audited by construction, needs no schema change, and a
fresh install (no events) starts open. The optional message a Family sees is
the Administrator's own bounded text, stored in the event's reviewed
``review_reason`` context field (at most 500 characters, no email addresses).

Every web process reads the state on Family requests, so a short in-process
cache keeps that to about one indexed query per process every few seconds;
turning the switch on or off takes effect everywhere within that time, with
no restart. The process that changes the switch clears its own cache, but
another thread in that process that read the old state just before the commit
can write it back, so even there the change can take up to CACHE_SECONDS.
Requests already in progress when the portal closes still finish, so the Admin
page asks the Administrator to wait about 10 seconds before changing data;
that wait also covers the cache delay above.

While the portal is closed, Family pages answer with a maintenance page and
the form's JSON endpoints with a clean 503, so no Family answer is written.
Keepalive and logout stay open (see OPEN_PATHS).
Email delivery is not paused by this switch: new receipts cannot arise (no
Family can submit), and scheduled invitations and reminders are paused, when
needed, with the existing Delivery controls, which the Admin page links to.
"""

import time
from dataclasses import dataclass

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.audit.schemas import Action, ActorKind
from parishkit.stewardship.audit.services import record_action

from .runtime_models import SystemConfiguration

# How long a web process may reuse the state it last read.
CACHE_SECONDS = 3
# Browsers and clients are told to try again after this many seconds.
RETRY_AFTER_SECONDS = 120
MESSAGE_LIMIT = 500
EVENTS = (
    Action.FAMILY_MAINTENANCE_STARTED.value,
    Action.FAMILY_MAINTENANCE_ENDED.value,
)
# Family form endpoints that answer scripts with JSON. The keepalive and
# logout endpoints stay open so a Family can keep, or end, their own session
# across a short maintenance window. They still write: keepalive refreshes the
# FamilySession row (but skips the FamilyCampaign activity bump while closed,
# see family_authentication.authenticated_family), and logout revokes the
# session, audits it and cancels its baselines. No Family answer is written.
JSON_PATHS = frozenset({"/family/form", "/family/submit", "/family/presence"})
OPEN_PATHS = frozenset({"/family/keepalive", "/family/logout"})


@dataclass(frozen=True)
class MaintenanceState:
    """Whether the Family portal is closed, since when, and the Admin's note."""

    closed: bool = False
    message: str = ""
    since: object = None
    actor_id: object = None


_cache = {"at": None, "state": MaintenanceState()}


def current_state(*, cached=True):
    """Return the switch's state from the newest maintenance audit event."""
    now = time.monotonic()
    if cached and _cache["at"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return _cache["state"]
    event = (
        AuditEvent.objects.filter(event_type__in=EVENTS)
        .order_by("-created_at", "-id")
        .values("id", "event_type", "created_at", "actor_id")
        .first()
    )
    state = MaintenanceState()
    if event and event["event_type"] == Action.FAMILY_MAINTENANCE_STARTED.value:
        context = (
            AuditContext.objects.filter(event_id=event["id"])
            .values_list("context", flat=True)
            .first()
        ) or {}
        state = MaintenanceState(
            closed=True,
            message=context.get("review_reason") or "",
            since=event["created_at"],
            actor_id=event["actor_id"],
        )
    _cache.update(at=now, state=state)
    return state


def clean_message(value):
    """Validate the optional note shown to Families (bounded, no addresses)."""
    text = " ".join(str(value or "").split())
    if len(text) > MESSAGE_LIMIT:
        raise ValueError("The maintenance message is too long.")
    if "@" in text:
        raise ValueError("The maintenance message cannot contain an email address.")
    return text


def set_closed(actor, *, closed, message=""):
    """Record an audited open/close change; a no-op change is refused."""
    text = clean_message(message) if closed else ""
    with transaction.atomic():
        # Serialize changes on the single configuration row, so two
        # Administrators closing at once cannot both pass the no-op check and
        # leave one note silently replaced by the other.
        list(SystemConfiguration.objects.select_for_update().values_list("pk"))
        if current_state(cached=False).closed == closed:
            raise ValueError("The Family portal is already in that state.")
        record_action(
            Action.FAMILY_MAINTENANCE_STARTED
            if closed
            else Action.FAMILY_MAINTENANCE_ENDED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor.identity,
            context={"review_reason": text} if text else None,
        )
    _cache.update(at=None)


def family_response(request, state):
    """The 503 a closed Family portal gives: JSON for the form script, else a page."""
    if request.path_info in JSON_PATHS:
        response = JsonResponse(
            {"maintenance": True, "message": state.message}, status=503
        )
    else:
        # Try again repeats a GET (a personal link must not fall back to code
        # sign-in); anything else starts over from the home page.
        retry = request.get_full_path() if request.method in {"GET", "HEAD"} else "/"
        response = render(
            request,
            "stewardship/family-maintenance.html",
            {"message": state.message, "retry": retry},
            status=503,
        )
    response["Retry-After"] = str(RETRY_AFTER_SECONDS)
    response["Cache-Control"] = "no-store"
    response.stewardship_safe_error = True
    return response


def gates(request):
    """True for Family routes the switch closes (keepalive and logout stay open)."""
    path = request.path_info
    if path in OPEN_PATHS:
        return False
    return path == "/" or path.startswith("/access/") or path.startswith("/family/")
