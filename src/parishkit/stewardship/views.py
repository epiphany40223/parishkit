"""Intentional scaffold responses; no authentication or campaign data access."""

from django.http import HttpRequest, HttpResponse
from django.views.decorators.http import require_safe


def _plain_response(text: str, status: int) -> HttpResponse:
    """Return non-cacheable text without reflecting request or configuration data."""
    response = HttpResponse(text, status=status, content_type="text/plain")
    response["Cache-Control"] = "no-store"
    return response


@require_safe
def unavailable(request: HttpRequest, **kwargs: str) -> HttpResponse:
    """Deny unimplemented human-facing functionality, including token exchange."""
    return _plain_response("This system is not configured yet.\n", 503)


@require_safe
def live(request: HttpRequest) -> HttpResponse:
    """Confirm only that the process can serve a request; query no dependencies."""
    return _plain_response("ok\n", 200)


@require_safe
def ready(request: HttpRequest) -> HttpResponse:
    """Remain unready until the real startup-phase checks have been implemented."""
    return _plain_response("unavailable\n", 503)


@require_safe
def metrics(request: HttpRequest) -> HttpResponse:
    """Expose nothing before the internal metrics authentication task lands."""
    return _plain_response("Not Found\n", 404)
