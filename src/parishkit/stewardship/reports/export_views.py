"""Requester-scoped report endpoints, separate from Admin-only operational jobs.

These closed participation endpoints are the export substrate, not the Phase 5
report catalog. Requests and one-use grants are POST bodies, never URL secrets.
All policy is reloaded by services and again inside the download read guard.
"""

from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, Outcome
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.web.responses import campaign_response

from .artifacts import ArtifactChunks, ArtifactReceipt
from .export_models import ExportPublication
from .export_services import (
    ExportConflict,
    admit_campaign,
    audit,
    authorize,
    cancel_export,
    consume_download,
    create_export,
    export_status,
    issue_download,
)
from .facts import FactUnavailable

CONTENT_TYPES = {"csv": "text/csv", "pdf": "application/pdf", "png": "image/png"}
SAFE_FAILURES = (
    ConfigError,
    DatabaseError,
    LimiterUnavailable,
    ObjectDoesNotExist,
    PermissionError,
    ReadUnavailable,
    FactUnavailable,
)


def _json(value, *, status=200):
    """Even status responses disclose private work and must not enter shared caches."""
    return JsonResponse(value, status=status, headers={"Cache-Control": "no-store"})


def _body(request, fields):
    """Reject unknown/duplicate fields; identifiers and filters stay out of URLs."""
    supplied = set(request.POST) - {"csrfmiddlewaretoken"}
    if (
        request.GET
        or supplied != set(fields)
        or any(len(request.POST.getlist(field)) != 1 for field in request.POST)
    ):
        raise ValueError("Invalid export request fields.")
    return {field: request.POST[field] for field in fields}


def _principal(request, store, *, read_only=False):
    """Anonymous, expired and Ministry-only sessions fail before identity access."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.CAMPAIGN_REPORT):
        raise PermissionError("This export is unavailable.")
    return principal


@require_POST
def create(request, campaign_id):
    """Accept only the compiled report's finite format/timezone/input vocabulary."""
    try:
        service = runtime()
        principal = _principal(request, service.store)
        values = _body(
            request, {"fact_set_id", "format", "browser_timezone", "request_key"}
        )
        result = create_export(
            service.store,
            principal.identity,
            campaign_id=campaign_id,
            fact_set_id=UUID(values["fact_set_id"]),
            format=values["format"],
            browser_timezone=values["browser_timezone"],
            request_key=UUID(values["request_key"]),
        )
        return _json({"id": str(result.pk)}, status=202)
    except ValueError:
        return _json({"error": "Invalid export request."}, status=400)
    except SAFE_FAILURES:
        return denial()


@require_GET
def status(request, request_id):
    """Knowing another job UUID never permits viewing its status or task journal."""
    try:
        if request.GET:
            raise ValueError("Export status does not accept query parameters.")
        service = runtime()
        principal = _principal(request, service.store)
        return _json(export_status(service.store, principal.identity, request_id))
    except ValueError:
        return _json({"error": "Invalid export request."}, status=400)
    except SAFE_FAILURES:
        return denial()


@require_POST
def cancel(request, request_id):
    """Cancellation becomes durable before the worker next reaches a safe point."""
    try:
        _body(request, set())
        service = runtime()
        principal = _principal(request, service.store)
        cancel_export(service.store, principal.identity, request_id)
        return _json({"id": str(request_id), "state": "cancelled"})
    except ExportConflict:
        return _json({"error": "Export cannot be cancelled."}, status=409)
    except ValueError:
        return _json({"error": "Invalid export request."}, status=400)
    except SAFE_FAILURES:
        return denial()


@require_POST
def download_grant(request, request_id):
    """Grant possession alone is insufficient: consumption still requires its user."""
    try:
        _body(request, set())
        service = runtime()
        principal = _principal(request, service.store)
        grant = issue_download(service.store, principal.identity, request_id)
        return _json(
            {"grant": str(grant.pk), "expires_at": grant.expires_at.isoformat()}
        )
    except ValueError:
        return _json({"error": "Invalid export request."}, status=400)
    except SAFE_FAILURES:
        return denial()


@require_POST
def download(request):
    """Serve bytes in-app with bounded download admission through response close."""
    finish, handed_off = None, False
    try:
        service = runtime()
        principal = _principal(request, service.store)
        values = _body(request, {"grant"})
        publication = consume_download(
            service.store, principal.identity, UUID(values["grant"])
        )
        job = publication.request
        finalized = False

        def finish(completed):
            """Server exhaustion is not proof of receipt by the browser."""
            nonlocal finalized
            if not finalized:
                finalized = True
                with transaction.atomic():
                    audit(
                        Action.EXPORT_DOWNLOADED,
                        job,
                        principal.identity,
                        outcome=Outcome.SUCCEEDED if completed else Outcome.FAILED,
                        count=publication.row_count,
                    )

        def fresh(guard):
            """Recheck session and artifact on the dedicated read connection."""
            current = _principal(request, service.store, read_only=True)
            if current.identity != principal.identity:
                raise ReadUnavailable("This export is unavailable.")
            authorize(service.store, current.identity, request=job)
            admit_campaign(job.campaign_id, mutating=False)
            retained = ExportPublication.objects.get(pk=publication.pk)
            if retained.expires_at <= database_now():
                raise ReadUnavailable("This export has expired.")

        def content():
            """Open before response construction, inside the read guard."""
            from django.conf import settings

            root = getattr(settings, "STEWARDSHIP_REPORTS_ROOT", None)
            if root is None:
                raise ConfigError("Export storage is not configured.")
            return ArtifactChunks(
                root,
                job.campaign_id,
                ArtifactReceipt(
                    publication.attempt_id, publication.size, publication.sha256
                ),
            )

        response = campaign_response(
            request,
            [job.campaign_id],
            authorize=fresh,
            open_content=content,
            filename=f"participation.{job.format}",
            content_type=CONTENT_TYPES[job.format],
            on_close=finish,
        )
        handed_off = response.status_code == 200 and response.streaming
        return response
    except ValueError:
        return _json({"error": "Invalid download grant."}, status=400)
    except SAFE_FAILURES:
        return denial()
    finally:
        if finish is not None and not handed_off:
            finish(False)
