"""The Admin page that closes and reopens the Family portal (see family_maintenance).

Reading the page needs a current Administrator. Changing the switch is a POST
with a CSRF token that also needs a fresh Google sign-in, like other
privileged commands, and is recorded in the audit log.
"""

from django.core import signing
from django.db import DatabaseError
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.web.refusals import with_default_code

from . import family_maintenance
from .admin_editing import error_response, principal
from .authentication import runtime
from .limiting import LimiterUnavailable
from .policy_models import PortalUser
from .runtime_models import SystemConfiguration
from .sessions import freshness, require_fresh
from .setup_views import _closed


def _page(request, *, message="", error=None):
    """Render the switch, with the reader's message and its error at the field.

    ``fresh`` shows each change control unavailable, named by the sign-in
    step it waits for, rather than refused after submit (#563); the POST
    still calls ``require_fresh``. A refused message comes back in its field
    with the server's reason beside it (400), not on an error page.
    """
    state = family_maintenance.current_state(cached=False)
    campaign = SystemConfiguration.objects.values_list(
        "current_campaign_id", "mode"
    ).first()
    campaign_id, mode = campaign or (None, None)
    response = render(
        request,
        "stewardship/family-portal-maintenance.html",
        {
            "state": state,
            "closed_by": PortalUser.objects.filter(pk=state.actor_id)
            .values_list("email", flat=True)
            .first()
            if state.actor_id
            else None,
            "message_limit": family_maintenance.MESSAGE_LIMIT,
            "delivery_url": reverse("admin:delivery_control")
            if campaign_id and mode == "production"
            else None,
            "fresh": freshness(request)[0],
            "message": message,
            "message_error": error,
        },
        status=400 if error else 200,
    )
    response["Cache-Control"] = "no-store"
    if error:
        # The refusal page is this page, safe to show as it is (not the
        # generic "Invalid request." the middleware draws for other 400s).
        response.stewardship_safe_error = True
        # The refusal log's code for this page's own refusal (#1444).
        with_default_code(response)
    return response


@require_http_methods(["GET", "HEAD", "POST"])
def family_portal(request):
    """Show whether Families can use the portal, and let an Administrator switch it."""
    try:
        service = runtime()
        if request.method == "POST":
            _closed(request, {"action", "message"})
            action = request.POST.get("action")
            if action not in {"close", "open"}:
                raise ValueError("Unknown maintenance action.")
            actor = principal(request, service)
            message = request.POST.get("message", "")
            if action == "close":
                try:
                    family_maintenance.clean_message(message)
                except ValueError as error:
                    return _page(request, message=message, error=str(error))
            require_fresh(request)
            family_maintenance.set_closed(
                actor,
                closed=action == "close",
                message=message,
            )
            response = HttpResponseRedirect(reverse("admin:family_portal"))
            response["Cache-Control"] = "no-store"
            return response
        _closed(request, set())
        principal(request, service)
        return _page(request)
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        LookupError,
        PermissionError,
        ValueError,
        signing.BadSignature,
    ) as error:
        return error_response(error)
