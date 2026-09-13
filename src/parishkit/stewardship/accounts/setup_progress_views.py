"""Exact source-load progress; GET is passive and visible-page POST is bounded."""

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.web.contracts import filters

from .authentication import runtime
from .setup_progress import source_progress
from .setup_views import ERRORS, _checked, error_response


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
                {"progress": progress},
            )
        return _checked(request, service, response)
    except ERRORS as error:
        return error_response(error)
