"""Accessible native controls over the compiled participation export services."""

from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.jobs.storage import TaskRetryConflict
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.responses import campaign_response

from .export_models import ExportRequest
from .export_services import (
    ExportConflict,
    admit_campaign,
    authorize,
    cancel_export,
    create_export,
    export_status,
    issue_download,
    regenerate_export,
    retry_export,
)
from .export_views import SAFE_FAILURES, _body, _principal, download_with_grant
from .models import CampaignDailyFactSet
from .workspace import ReportQuery


def _redirect(identifier):
    """Native form submission uses a private Post/Redirect/Get handoff."""
    response = redirect("admin:report_export", request_id=identifier)
    response["Cache-Control"] = "no-store"
    return response


def _error(
    request, *, campaign_id=None, request_id=None, exact_id=None, status=409, busy=False
):
    """Fixed-text recovery never reflects a submitted value or internal failure."""
    # No request context processors: a database outage must not trigger another
    # database query while rendering its recovery response.
    response = HttpResponse(
        render_to_string(
            "stewardship/report-export-error.html",
            {
                "campaign_id": campaign_id,
                "request_id": request_id,
                "exact_id": exact_id,
                "busy": busy,
                "temporary": status == 503,
            },
        ),
        status=status,
    )
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    if status == 503:
        response["Retry-After"] = "5"
    return response


@require_POST
def create(request, campaign_id):
    """Pin the generation shown in the preview, including an explicitly stale one."""
    try:
        service = runtime()
        principal = _principal(request, service.store)
        values = _body(
            request, {"fact_set_id", "format", "browser_timezone", "request_key"}
        )
        job = create_export(
            service.store,
            principal.identity,
            campaign_id=campaign_id,
            fact_set_id=UUID(values["fact_set_id"]),
            format=values["format"],
            browser_timezone=values["browser_timezone"],
            request_key=UUID(values["request_key"]),
        )
        return _redirect(job.pk)
    except CampaignDailyFactSet.DoesNotExist:
        return _error(request, campaign_id=campaign_id, status=503)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(request, campaign_id=campaign_id, status=503)
    except ValueError:
        return _error(request, campaign_id=campaign_id, status=400)


@require_GET
def detail(request, request_id):
    """Passive status never renews login; query/render remain guarded until close."""
    try:
        service = runtime()
        principal = _principal(request, service.store, read_only=True)
        if request.GET:
            raise ValueError("Export status has no query fields.")
        # Only the lock identity is loaded before the barrier.
        campaign_id = ExportRequest.objects.values_list("campaign_id", flat=True).get(
            pk=request_id
        )

        def fresh(guard):
            """Neither request UUID nor earlier session access bypasses revocation."""
            current = _principal(request, service.store, read_only=True)
            if current.identity != principal.identity:
                raise PermissionError("Report access changed.")
            admit_campaign(campaign_id, mutating=False)
            authorize(
                service.store,
                current.identity,
                request=ExportRequest.objects.get(pk=request_id),
            )

        def content():
            """Safe job state and inputs come from the existing requester owner."""
            job = (
                ExportRequest.objects.select_related(
                    "fact_set", "configuration__parish", "information_snapshot"
                )
                .defer("information_snapshot__document")
                .annotate(
                    information_source_generation=F(
                        "information_snapshot__source__generation"
                    )
                )
                .get(pk=request_id)
            )
            state = export_status(service.store, principal.identity, request_id)
            mutable = True
            try:
                admit_campaign(campaign_id, mutating=True)
            except PermissionError:
                mutable = False
            context = {
                "job": job,
                "status": state,
                "mutable": mutable,
                "retry_key": uuid4(),
                "report_title": "Additional-information export"
                if job.report == "additional_information"
                else "Participation export",
                "report_url": reverse("admin:information_queue", args=(campaign_id,))
                if job.report == "additional_information"
                else ReportQuery(
                    scope=job.parameters["population_scope"],
                    timezone=job.browser_timezone,
                ).url(campaign_id),
                "can_cancel": state["state"] in {"queued", "running", "retry_wait"},
            }
            return iter(
                (
                    render_to_string(
                        "stewardship/report-export.html", context, request=request
                    ).encode(),
                )
            )

        return campaign_response(
            request, [campaign_id], authorize=fresh, open_content=content
        )
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(request, request_id=request_id, status=503)
    except ValueError:
        return _error(request, request_id=request_id, status=400)


@require_POST
def command(request, request_id, *, action):
    """Closed route-selected actions share cancellation/retry/download authority."""
    try:
        service = runtime()
        principal = _principal(request, service.store)
        values = _body(
            request, {"request_key"} if action in {"retry", "regenerate"} else set()
        )
        if action == "cancel":
            cancel_export(service.store, principal.identity, request_id)
        elif action == "retry":
            retry_export(
                service.store,
                principal.identity,
                request_id,
                request_key=UUID(values["request_key"]),
            )
        elif action == "download":
            grant = issue_download(service.store, principal.identity, request_id)
            response = download_with_grant(request, service, principal, grant.pk)
            if response.status_code == 503:
                response.close()
                return _error(request, request_id=request_id, status=503, busy=True)
            return response
        elif action == "regenerate":
            result = regenerate_export(
                service.store,
                principal.identity,
                request_id,
                request_key=UUID(values["request_key"]),
            )
            return _redirect(result.pk)
        else:
            raise ValueError("Unknown report action.")
        return _redirect(request_id)
    except (ExportConflict, TaskRetryConflict):
        return _error(request, request_id=request_id)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(request, request_id=request_id, status=503)
    except ValueError:
        return _error(request, request_id=request_id, status=400)
