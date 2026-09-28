"""Admin/Staff talents and limitations report, with CSV and XLSX downloads (#247)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.responses import campaign_response

from .export_views import SAFE_FAILURES
from .read_admission import admit_report_read
from .talents import TalentQuery, talents_csv, talents_report, talents_xlsx

FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _principal(request, store, *, read_only=False, export=False):
    """Every request reloads current policy; Ministry leaders never qualify."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.CAMPAIGN_REPORT) or (
        export and not allows(principal, Capability.REPORT_EXPORT)
    ):
        raise PermissionError("The talents report is unavailable.")
    return principal


def _error(campaign_id, *, status):
    """No private filter or exception values, and a way back to the report."""
    debug_swallowed("report request refused")
    response = HttpResponse(
        render_to_string(
            "stewardship/talents-report-error.html",
            {"campaign_id": campaign_id, "status": status},
        ),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    if status == 503:
        response["Retry-After"] = "5"
    return response


def _audit(action, principal, campaign_id, outcome, count=0):
    """Retain access evidence as a count only, never a name or filter."""
    with transaction.atomic():
        system = SystemConfiguration.objects.select_related(
            "active_configuration__parish"
        ).get()
        record_action(
            action,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=principal.identity,
            subject_id=campaign_id,
            parish_id=system.active_configuration.parish.pk,
            campaign_id=campaign_id,
            context={"outcome": outcome, "count": count},
        )


def _respond(request, campaign_id, *, export, render):
    """Shared admission, purge protection, audit and failure handling.

    ``render(result, query, extra)`` returns the bytes; for a download,
    ``extra`` holds the format and display timezone chosen on the page.
    """
    finish, handed_off = None, False
    action = Action.TALENTS_REPORT_EXPORTED if export else Action.TALENTS_REPORT_VIEWED
    try:
        service = runtime()
        principal = _principal(request, service.store, export=export)
        admit_report_read(campaign_id)
        try:
            if request.GET:
                raise ValueError("Talent filters require private POST state.")
            parameters = request.POST.copy()
            parameters.pop("csrfmiddlewaretoken", None)
            extra = {}
            if export:
                fmt = parameters.pop("format", ["csv"])
                zone = parameters.pop("timezone", ["UTC"])
                if (
                    len(fmt) != 1
                    or fmt[0] not in FORMATS
                    or len(zone) != 1
                    or (zone[0] != "UTC" and zone[0] not in timezone_names())
                ):
                    raise ValueError("Invalid talent export choice.")
                extra = {"format": fmt[0], "zone": ZoneInfo(zone[0])}
            query = TalentQuery.parse(parameters)
        except ValueError:
            return _error(campaign_id, status=400)
        finalized, count = False, 0

        def finish(completed):
            """Audit completion once, after releasing the read transaction."""
            nonlocal finalized
            if finalized:
                return
            finalized = True
            try:
                _audit(
                    action,
                    principal,
                    campaign_id,
                    Outcome.SUCCEEDED if completed else Outcome.FAILED,
                    count,
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """A role change while the report is produced ends it."""
            nonlocal principal
            fresh = _principal(request, service.store, read_only=True, export=export)
            if fresh.identity != principal.identity:
                raise PermissionError("Talent report access changed.")
            principal = fresh
            admit_report_read(campaign_id)

        def content():
            """Only detached authorized data reaches the template or file writer."""
            nonlocal count
            campaign = Campaign.objects.select_related("active_configuration").get(
                pk=campaign_id
            )
            result = talents_report(
                campaign_id,
                query,
                principal,
                configuration=campaign.active_configuration.values,
            )
            count = len(result["members"]) + len(result["families"])
            return iter((render(result, query, extra),))

        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
            **(
                {
                    "filename": f"stewardship-talents-{stamp}.{extra['format']}",
                    "content_type": FORMATS[extra["format"]],
                }
                if export
                else {}
            ),
        )
        if response.status_code == 503 and not response.streaming:
            return _error(campaign_id, status=503)
        handed_off = response.status_code == 200 and response.streaming
        return response
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(campaign_id, status=503)
    except ValueError as error:
        emit_failure(error, event=Event.REPORT_SHAPING_FAILED)
        return _error(campaign_id, status=503)
    finally:
        if finish is not None and not handed_off:
            finish(False)


@require_http_methods(["GET", "POST"])
def report(request, campaign_id):
    """Default GET shows everything; search and talent filters use POST."""

    def render(result, query, extra):
        """The native report page."""
        return render_to_string(
            "stewardship/talents-report.html",
            result
            | {
                "campaign_id": campaign_id,
                "query": query,
                "query_fields": query.form_values(),
                "export_timezones": sorted(timezone_names()),
            },
            request=request,
        ).encode()

    return _respond(request, campaign_id, export=False, render=render)


@require_http_methods(["POST"])
def export(request, campaign_id):
    """Download the filtered report as CSV or XLSX, rendered on request."""

    def render(result, query, extra):
        """The chosen file format, with times in the chosen timezone."""
        if extra["format"] == "xlsx":
            return talents_xlsx(result, extra["zone"])
        return talents_csv(result, extra["zone"])

    return _respond(request, campaign_id, export=True, render=render)
