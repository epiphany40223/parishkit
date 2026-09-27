"""Admin session deadline status and the explicit "Stay signed in" renewal.

Every Admin page, including the initial-setup wizard, carries one shared
inactivity warning. Its script reads the deadlines from the page, re-syncs
them here without renewing anything, and renews idle time only when the Admin
presses "Stay signed in". Renewal is ordinary Admin activity: it moves the
session's idle deadline (which is also the setup attempt's idle deadline) and
never the absolute lifetime.
"""

from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_safe

from parishkit.config import ConfigError

from .authentication import runtime
from .limiting import LimiterUnavailable
from .session_policy import ADMIN_IDLE
from .sessions import authenticated_admin, database_now


def _deadlines(request, *, activity):
    """Return the current deadlines, or a fixed status when there is no session.

    Status reads are read-only, so they can neither renew nor revoke. The
    access gate lets both routes through in every portal state (setup,
    finishing, maintenance), so the views authenticate here themselves.
    """
    try:
        service = runtime()
        principal = authenticated_admin(
            request,
            store=service.store,
            activity=activity,
            read_only=not activity,
        )
    except (ConfigError, LimiterUnavailable):
        return JsonResponse({"state": "unavailable"}, status=503)
    if principal is None:
        return JsonResponse({"state": "signed_out"}, status=401)
    row = request.portal_session
    response = JsonResponse(
        {
            "state": "active",
            "server_now": database_now().isoformat(),
            "idle_deadline": min(
                row.expires_at, row.last_activity_at + ADMIN_IDLE
            ).isoformat(),
            "absolute_deadline": row.expires_at.isoformat(),
        }
    )
    response["Cache-Control"] = "no-store"
    return response


@require_safe
def session_status(request):
    """Passive re-sync for another tab's activity; never counts as activity."""
    return _deadlines(request, activity=False)


@require_POST
def session_renew(request):
    """CSRF-protected explicit activity that renews the idle deadline only."""
    return _deadlines(request, activity=True)
