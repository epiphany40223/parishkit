"""An Administrator acknowledges a security event from the dashboard.

The acknowledgement is one POST with a CSRF token under a current Administrator
session, recorded once with its audit, after which the dashboard is shown
again. Nothing about the event changes; only who has seen it.
"""

from django.core import signing
from django.db import DatabaseError, transaction
from django.http import HttpResponseRedirect
from django.views.decorators.http import require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import error_response, principal
from .authentication import runtime
from .configuration_installation import coherent_configuration
from .limiting import LimiterUnavailable
from .policy import Capability
from .security_events import acknowledge


@require_POST
def acknowledge_event(request, event_id):
    """Record the signed-in Administrator's acknowledgement and return home."""
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.MANAGE_USERS)
        filters(request.GET, allowed=set())
        with transaction.atomic():
            configuration = coherent_configuration(service.store)
            if configuration.restore_review_required:
                raise ConfigError("Configuration is unavailable.")
            acknowledge(
                event_id, actor, parish_id=configuration.active_configuration.parish.pk
            )
        response = HttpResponseRedirect("/admin/")
        fresh = principal(
            request, service, read_only=True, capability=Capability.MANAGE_USERS
        )
        if fresh.identity != actor.identity:
            raise PermissionError("Acknowledging Administrator changed.")
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
