"""An Administrator acknowledges the critical-events banner for every Admin.

The acknowledgement is one POST with a CSRF token under a current Administrator
session (the same System logs authority that reads the events), recorded with
its audit in one transaction, after which the Admin home page is shown again.
It acknowledges only the rows whose ids the rendered banner signed into its
form; an altered list is refused. No log row changes; a newer CRITICAL event
brings the banner back.
"""

from django.core import signing
from django.db import DatabaseError, transaction
from django.http import HttpResponseRedirect
from django.views.decorators.http import require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.audit.critical_events import WINDOW, acknowledge, shown
from parishkit.stewardship.web.contracts import filters

from .admin_editing import error_response, principal
from .authentication import runtime
from .configuration_installation import coherent_configuration
from .limiting import LimiterUnavailable
from .policy import Capability
from .sessions import database_now


@require_POST
def acknowledge_critical_events(request):
    """Record the signed-in Administrator's acknowledgement and return home."""
    try:
        service = runtime()
        actor = principal(request, service, capability=Capability.SYSTEM_LOGS)
        filters(request.GET, allowed=set())
        ids = shown(request.POST.get("shown"))
        with transaction.atomic():
            configuration = coherent_configuration(service.store)
            if configuration.restore_review_required:
                raise ConfigError("Configuration is unavailable.")
            acknowledge(
                actor.identity,
                ids=ids,
                since=database_now() - WINDOW,
                parish_id=configuration.active_configuration.parish.pk,
            )
        response = HttpResponseRedirect("/admin/")
        fresh = principal(
            request, service, read_only=True, capability=Capability.SYSTEM_LOGS
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
