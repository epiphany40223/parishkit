"""A closed original-login cancellation surface, including YAML selection gaps."""

from uuid import UUID

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.storage import StaleRecordError

from .admin_editing import error_response
from .authentication import runtime
from .policy import Capability, allows
from .sessions import authenticated_admin
from .setup_cancellation import cancel_finalizing_setup, cancellation_status
from .setup_finalization_status import finalization_status
from .setup_views import ERRORS, _closed


def _completed_redirect(request, service):
    """Completion restores ordinary current-policy checks, never predecessor access."""
    actor = authenticated_admin(request, store=service.store)
    if not allows(actor, Capability.CONFIGURE):
        raise PermissionError("Setup progress requires an Administrator.")
    response = HttpResponseRedirect("/admin/")
    response["Cache-Control"] = "no-store"
    return response


def _progress(request, service):
    """A completion racing either read hands off through ordinary authorization."""
    try:
        progress = finalization_status(request, service)
        response = render(request, "stewardship/setup-cancel.html", progress)
        if cancellation_status(request, service) != progress["attempt"]:
            raise StaleRecordError("Setup changed while rendering.")
        return response
    except PermissionError:
        if service.configured():
            return _completed_redirect(request, service)
        raise


@require_http_methods(["GET", "HEAD", "POST"])
def setup_cancellation(request):
    """No draft, credential or contact values are returned across this exception."""
    try:
        service = runtime()
        _closed(request, {"attempt"})
        if request.method != "POST" and service.configured():
            return _completed_redirect(request, service)
        if request.method == "POST":
            cancel_finalizing_setup(
                request, service, UUID(request.POST.get("attempt", ""))
            )
            response = HttpResponseRedirect("/admin/setup/cancel")
        else:
            response = _progress(request, service)
        response["Cache-Control"] = "no-store"
        return response
    except ERRORS as error:
        return error_response(error)
