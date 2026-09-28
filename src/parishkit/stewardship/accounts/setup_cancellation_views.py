"""The original login's "Finishing setup" page and its cancellation surface.

It stays usable across setup's YAML selection gap, which is why the access gate
exempts this one path and the view authenticates the original login itself.
"""

from uuid import UUID

from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.storage import StaleRecordError

from .admin_editing import error_response
from .authentication import runtime
from .policy import Capability, allows
from .sessions import authenticated_admin
from .setup_cancellation import cancel_finalizing_setup, cancellation_status
from .setup_finalization_status import finalization_status
from .setup_finishing import finishing
from .setup_views import ERRORS, _closed


def _completed_redirect(request, service, *, polled=False):
    """Completion restores ordinary current-policy checks, never predecessor access.

    A page load goes to the admin home. The finishing page's status poll
    instead learns that setup is complete, so the page can show its Continue
    link without leaving.
    """
    actor = authenticated_admin(request, store=service.store)
    if not allows(actor, Capability.CONFIGURE):
        raise PermissionError("Setup progress requires an Administrator.")
    if polled:
        response = JsonResponse(
            {"overall": "completed", "signature": "completed", "continue": "/admin/"}
        )
    else:
        response = HttpResponseRedirect("/admin/")
    response["Cache-Control"] = "no-store"
    return response


def _progress(request, service, *, polled=False):
    """Render the "Finishing setup" page, or its status for the page's poll.

    Both are passive reads: neither renews the original login's deadlines.
    A completion racing either read hands off through ordinary authorization.
    """
    try:
        progress = finalization_status(request, service)
        steps = finishing(progress)
        if polled:
            response = JsonResponse(
                {"overall": steps["overall"], "signature": steps["signature"]}
            )
        else:
            response = render(
                request,
                "stewardship/setup-cancel.html",
                progress
                | steps
                | {
                    # Return here after the step-up sign-in.
                    "next": reverse("admin:setup_cancel"),
                    "status_url": reverse("admin:setup_cancel") + "?format=json",
                },
            )
        if cancellation_status(request, service) != progress["attempt"]:
            raise StaleRecordError("Setup changed while rendering.")
        return response
    except PermissionError:
        if service.configured():
            return _completed_redirect(request, service, polled=polled)
        raise


@require_http_methods(["GET", "HEAD", "POST"])
def setup_cancellation(request):
    """No draft, credential or contact values are returned across this exception."""
    try:
        service = runtime()
        polled = request.method != "POST" and bool(request.GET)
        _closed(
            request,
            {"attempt"},
            query={"format"} if request.method != "POST" else frozenset(),
        )
        if polled and request.GET.get("format") != "json":
            raise ValueError("Invalid progress format.")
        if request.method != "POST" and service.configured():
            return _completed_redirect(request, service, polled=polled)
        if request.method == "POST":
            cancel_finalizing_setup(
                request, service, UUID(request.POST.get("attempt", ""))
            )
            response = HttpResponseRedirect("/admin/setup/cancel")
        else:
            response = _progress(request, service, polled=polled)
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)
