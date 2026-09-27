"""Exact source-load progress; GET is passive and visible-page POST is bounded."""

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.web.contracts import filters

from .authentication import runtime
from .setup_drafts import view_draft
from .setup_progress import source_progress
from .setup_views import ERRORS, _checked, error_response, page_error
from .setup_wizard import wizard_for


@require_http_methods(["GET", "HEAD", "POST"])
def setup_source_progress(request, task_id):
    """No posted heartbeat, deadline, worker or claimed version is trusted."""
    try:
        query = filters(request.GET, allowed={"format"})
        if query and query != {"format": "json"}:
            raise ValueError("Invalid progress format.")
        if (
            request.FILES
            or set(request.POST) - {"csrfmiddlewaretoken"}
            or any(len(values) != 1 for _, values in request.POST.lists())
        ):
            raise ValueError("Invalid source progress fields.")
        service = runtime()
        progress = source_progress(
            request, service, task_id, renew=request.method == "POST"
        )
        if query:
            response = JsonResponse(progress)
        else:
            response = render(
                request,
                "stewardship/setup-source-progress.html",
                {
                    "progress": progress,
                    "wizard": _wizard(request, service),
                },
            )
        return _checked(request, service, response)
    except ERRORS as error:
        if request.GET:
            return error_response(error)
        return page_error(request, error, "source")


def _wizard(request, service):
    """Show the stepper when this sign-in's draft is still readable.

    The progress page must keep rendering for failed or expired loads, whose
    draft may no longer be viewable, so an unavailable draft omits the stepper.
    """
    try:
        return wizard_for(view_draft(request, service), "source")
    except ERRORS:
        return None
