"""Closed requester endpoints for exports whose input generation is not ready yet."""

from uuid import UUID

from django.views.decorators.http import require_GET, require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.jobs.storage import TaskRetryConflict

from .exact_services import (
    cancel_exact_export,
    create_exact_export,
    exact_export_status,
    retry_exact_export,
)
from .export_services import ExportConflict
from .export_views import SAFE_FAILURES, _body, _json, _principal


@require_POST
def create(request, campaign_id):
    """Capture current inputs server-side; never accept caller-chosen SQL cutoffs."""
    try:
        service = runtime()
        principal = _principal(request, service.store)
        values = _body(
            request, {"population_scope", "format", "browser_timezone", "request_key"}
        )
        result = create_exact_export(
            service.store,
            principal.identity,
            campaign_id=campaign_id,
            population_scope=values["population_scope"],
            format=values["format"],
            browser_timezone=values["browser_timezone"],
            request_key=UUID(values["request_key"]),
        )
        return _json({"id": str(result.pk)}, status=202)
    except SAFE_FAILURES:
        return denial()
    except ValueError:
        return _json({"error": "Invalid export request."}, status=400)


@require_GET
def status(request, request_id):
    """Only the original requester or a current Admin may inspect this job."""
    try:
        if request.GET:
            raise ValueError("Export status does not accept query parameters.")
        service = runtime()
        principal = _principal(request, service.store)
        return _json(exact_export_status(service.store, principal.identity, request_id))
    except SAFE_FAILURES:
        return denial()
    except ValueError:
        return _json({"error": "Invalid export request."}, status=400)


@require_POST
def cancel(request, request_id):
    """Cancellation races with handoff through the common work-order boundary."""
    try:
        _body(request, set())
        service = runtime()
        principal = _principal(request, service.store)
        cancel_exact_export(service.store, principal.identity, request_id)
        return _json({"id": str(request_id), "state": "cancelled"})
    except ExportConflict:
        return _json({"error": "Export cannot be cancelled."}, status=409)
    except SAFE_FAILURES:
        return denial()
    except ValueError:
        return _json({"error": "Invalid export request."}, status=400)


@require_POST
def retry(request, request_id):
    """Link a fresh retry command without substituting inputs or resetting history."""
    try:
        values = _body(request, {"request_key"})
        service = runtime()
        principal = _principal(request, service.store)
        retry_exact_export(
            service.store,
            principal.identity,
            request_id,
            request_key=UUID(values["request_key"]),
        )
        return _json({"id": str(request_id)}, status=202)
    except TaskRetryConflict:
        return _json({"error": "Export cannot be retried."}, status=409)
    except SAFE_FAILURES:
        return denial()
    except ValueError:
        return _json({"error": "Invalid export request."}, status=400)
