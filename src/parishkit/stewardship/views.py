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
    """Report only readiness, never a setup phase, identity, path or exception."""
    from django.conf import settings

    from .runtime_health import RuntimeHealth

    runtime = getattr(settings, "STEWARDSHIP_HEALTH_RUNTIME", None)
    ready = isinstance(runtime, RuntimeHealth) and all(runtime.checks().values())
    return _plain_response("ok\n" if ready else "unavailable\n", 200 if ready else 503)


@require_safe
def metrics(request: HttpRequest) -> HttpResponse:
    """Require both internal ingress admission and constant-time bearer comparison."""
    from django.conf import settings

    from .runtime_health import RuntimeHealth

    runtime = getattr(settings, "STEWARDSHIP_HEALTH_RUNTIME", None)
    if not isinstance(runtime, RuntimeHealth) or not runtime.authorized_metrics(
        request.META.get("HTTP_AUTHORIZATION")
    ):
        return _plain_response("Not Found\n", 404)
    try:
        result = runtime.metrics()
    except Exception:
        return _plain_response("unavailable\n", 503)
    response = _plain_response(result, 200)
    response["Content-Type"] = "text/plain; version=0.0.4; charset=utf-8"
    return response
