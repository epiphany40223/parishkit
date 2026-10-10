"""Shared export view helpers and the Admin-only export cleanup retry.

The export pages (export_ui, exact_ui and the report export forms) and the
Admin automation command line share the admission (``_principal``), field
parsing (``_body``) and the guarded one-use download (``download_with_grant``)
here. The JSON export API that once lived here was retired unused (#758).
All policy is reloaded by services and again inside the download read guard.
"""

from dataclasses import dataclass
from uuid import UUID

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.admin_caller import AdminCaller
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, Outcome
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.storage import TaskRetryConflict
from parishkit.stewardship.jobs.task_retries import retry_export_cleanup_task
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.responses import campaign_response

from .artifacts import ArtifactChunks, ArtifactReceipt
from .export_models import ExportPublication
from .export_services import (
    admit_campaign,
    audit,
    authorize,
    consume_download,
)
from .facts import FactUnavailable

CONTENT_TYPES = {
    "csv": "text/csv",
    "pdf": "application/pdf",
    "png": "image/png",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
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


def _principal(request, store, *, read_only=False, ministry_jobs=False):
    """Job entry admits leaders; request-specific services still enforce ownership."""
    from .ministries import can_report

    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.CAMPAIGN_REPORT) and not (
        ministry_jobs and can_report(principal)
    ):
        raise PermissionError("This export is unavailable.")
    return principal


@require_POST
def retry_cleanup_command(request, task_id):
    """An Admin retries only the selected failed cleanup, with a replay-safe POST."""
    try:
        service = runtime()
        principal = _principal(AdminCaller.from_request(request), service.store)
        if not allows(principal, Capability.BACKGROUND_WORK):
            raise PermissionError("Export cleanup requires an Administrator.")
        values = _body(request, {"request_key"})
        result = retry_export_cleanup_task(
            service.store,
            principal,
            task_id,
            request_key=UUID(values["request_key"]),
        )
        response = redirect("admin:background_task_page", task_id=result.run_id)
        response["Cache-Control"] = "no-store"
        return response
    except TaskRetryConflict:
        return _cleanup_error(request, task_id, status=409)
    except StorageInvariantError:
        return _json({"error": "Export cleanup is unavailable."}, status=503)
    except SAFE_FAILURES:
        return denial()
    except ValueError:
        return _cleanup_error(request, task_id, status=400)


def _cleanup_error(request, task_id, *, status):
    """A conventional form failure needs an accessible recovery page, not JSON."""
    response = render(
        request,
        "stewardship/export-cleanup-error.html",
        {"task_id": task_id, "conflict": status == 409},
        status=status,
    )
    # Only fixed text and the router's parsed UUID reach this typed error page.
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    return response


@dataclass(frozen=True)
class Download:
    """A consumed download grant and what its guarded read needs.

    ``authorize`` is the fresh check the campaign read guard runs,
    ``open_content`` opens the stored file inside that guard, and
    ``finish(completed)`` records the closing ``export_downloaded`` once.
    """

    publication: object
    job: object
    file_name: str
    content_type: str
    authorize: object
    open_content: object
    finish: object


def prepare_download(subject, service, principal, grant_id):
    """Consume one download grant and return what its guarded read needs.

    ``subject`` is the request (the pages) or an ``AdminCaller`` (the
    command line's ``export download``); the fresh check admits it again
    inside the read guard. Consuming the grant records the opening
    ``export_downloaded``; the caller must call ``finish`` exactly as the
    page's response does, whether or not the bytes were all sent.
    """
    publication = consume_download(service.store, principal.identity, grant_id)
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
        current = _principal(subject, service.store, read_only=True, ministry_jobs=True)
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

    return Download(
        publication=publication,
        job=job,
        file_name=f"{job.report}.{job.format}",
        content_type=CONTENT_TYPES[job.format],
        authorize=fresh,
        open_content=content,
        finish=finish,
    )


def download_with_grant(request, service, principal, grant_id):
    """Consume a guarded one-use grant and stream the export inside its read guard."""
    finish, handed_off = None, False
    try:
        download = prepare_download(request, service, principal, grant_id)
        finish = download.finish
        response = campaign_response(
            request,
            [download.job.campaign_id],
            authorize=download.authorize,
            open_content=download.open_content,
            filename=download.file_name,
            content_type=download.content_type,
            on_close=finish,
        )
        handed_off = response.status_code == 200 and response.streaming
        return response
    finally:
        if finish is not None and not handed_off:
            finish(False)
