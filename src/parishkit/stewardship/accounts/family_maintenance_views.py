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

from . import family_maintenance
from .admin_editing import error_response, principal
from .authentication import runtime
from .limiting import LimiterUnavailable
from .policy_models import PortalUser
from .runtime_models import SystemConfiguration
from .sessions import require_fresh
from .setup_views import _closed


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
            require_fresh(request)
            family_maintenance.set_closed(
                actor,
                closed=action == "close",
                message=request.POST.get("message", ""),
            )
            response = HttpResponseRedirect(reverse("admin:family_portal"))
            response["Cache-Control"] = "no-store"
            return response
        _closed(request, set())
        principal(request, service)
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
            },
        )
        response["Cache-Control"] = "no-store"
        return response
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
