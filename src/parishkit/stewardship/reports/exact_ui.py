"""Native requester controls over immutable queued participation calculations."""

from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.jobs.storage import TaskRetryConflict
from parishkit.stewardship.source.snapshot_models import SourceSnapshot
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.responses import campaign_response

from .exact_models import ExactExportRequest
from .exact_services import (
    cancel_exact_export,
    create_exact_export,
    exact_export_status,
    retry_exact_export,
)
from .export_services import ExportConflict, admit_campaign, authorize
from .export_ui import _error
from .export_views import SAFE_FAILURES, _body, _principal
from .read_admission import admit_report_read
from .workspace import ReportQuery


def _redirect(identifier):
    """Keep native mutation responses private and safe to reload."""
    response = redirect("admin:report_exact", request_id=identifier)
    response["Cache-Control"] = "no-store"
    return response


@require_POST
def create(request, campaign_id):
    """Freeze current inputs server-side, even before a ready chart exists."""
    try:
        service = runtime()
        principal = _principal(request, service.store)
        values = _body(
            request, {"population_scope", "format", "browser_timezone", "request_key"}
        )
        job = create_exact_export(
            service.store,
            principal.identity,
            campaign_id=campaign_id,
            population_scope=values["population_scope"],
            format=values["format"],
            browser_timezone=values["browser_timezone"],
            request_key=UUID(values["request_key"]),
        )
        return _redirect(job.pk)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(request, campaign_id=campaign_id, status=503)
    except ValueError:
        return _error(request, campaign_id=campaign_id, status=400)


@require_GET
def detail(request, request_id):
    """Passive, guarded status retains the original inputs across worker handoff."""
    try:
        service = runtime()
        principal = _principal(request, service.store, read_only=True)
        if request.GET:
            raise ValueError("Export status has no query fields.")
        campaign_id = ExactExportRequest.objects.values_list(
            "campaign_id", flat=True
        ).get(pk=request_id)

        def fresh(guard):
            """Recheck the caller and request owner after the barrier is held."""
            current = _principal(request, service.store, read_only=True)
            if current.identity != principal.identity:
                raise PermissionError("Report access changed.")
            admit_report_read(campaign_id)
            authorize(
                service.store,
                current.identity,
                request=ExactExportRequest.objects.get(pk=request_id),
            )

        def content():
            """Only the existing owner chooses waiting/rendering/terminal status."""
            job = ExactExportRequest.objects.select_related(
                "timezone_configuration"
            ).get(pk=request_id)
            state = exact_export_status(service.store, principal.identity, request_id)
            mutable = True
            try:
                admit_campaign(campaign_id, mutating=True)
            except PermissionError:
                mutable = False
            context = {
                "job": job,
                # WEB may read public source metadata, not every manifest column.
                "source": SourceSnapshot.objects.values(
                    "generation", "promoted_at"
                ).get(pk=job.source_id),
                "status": state,
                "mutable": mutable,
                "retry_key": uuid4(),
                "can_cancel": state["state"] in {"queued", "running", "retry_wait"},
                "report_url": ReportQuery(
                    scope=job.population_scope, timezone=job.browser_timezone
                ).url(campaign_id),
            }
            return iter(
                (
                    render_to_string(
                        "stewardship/report-exact.html", context, request=request
                    ).encode(),
                )
            )

        return campaign_response(
            request, [campaign_id], authorize=fresh, open_content=content
        )
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(request, exact_id=request_id, status=503)
    except ValueError:
        return _error(request, exact_id=request_id, status=400)


@require_POST
def command(request, request_id, *, action):
    """Delegate pre/post-handoff cancellation and retry without replacing inputs."""
    try:
        service = runtime()
        principal = _principal(request, service.store)
        values = _body(request, {"request_key"} if action == "retry" else set())
        if action == "cancel":
            cancel_exact_export(service.store, principal.identity, request_id)
        elif action == "retry":
            retry_exact_export(
                service.store,
                principal.identity,
                request_id,
                request_key=UUID(values["request_key"]),
            )
        else:
            raise ValueError("Unknown report action.")
        return _redirect(request_id)
    except (ExportConflict, TaskRetryConflict):
        return _error(request, exact_id=request_id)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(request, exact_id=request_id, status=503)
    except ValueError:
        return _error(request, exact_id=request_id, status=400)
