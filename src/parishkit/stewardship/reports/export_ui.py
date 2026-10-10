"""Accessible native controls over the compiled participation export services."""

from urllib.parse import urlencode
from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from parishkit.stewardship.accounts import admin_navigation
from parishkit.stewardship.accounts.admin_editing import step_up_response
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.sessions import (
    FreshAuthenticationRequired,
    require_fresh,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queue_wait import queue_wait
from parishkit.stewardship.jobs.storage import TaskRetryConflict
from parishkit.stewardship.observability import debug_swallowed
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.responses import campaign_response

from .directories import REACH, testing_codes_context
from .export_models import ExportRequest
from .export_services import (
    ExportConflict,
    ExportExpired,
    ExportRequestBound,
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

# Exports whose new file needs a fresh Google sign-in (#547): the directory
# files carry live Family codes and the financial file financial detail.
FRESH_REPORTS = frozenset({"family_directory", "postal_outreach", "financial"})


def _regenerate_step_up(request, store, principal, request_id):
    """The step-up page when regenerating this export needs a fresh sign-in.

    A regenerated directory or financial file is a new copy of the same codes
    or financial detail, so it needs the same fresh sign-in as creating one
    (#547); the step-up returns to this export's status page. The export's
    owner and capability are checked first (``authorize``), so its kind is
    never revealed to anyone else. Returns None when it may be regenerated
    now; ``regenerate_export`` still rechecks everything under its lock.
    """
    export = ExportRequest.objects.only(
        "report", "requester_id", "authorization_scope"
    ).get(pk=request_id)
    authorize(store, principal.identity, request=export)
    if export.report not in FRESH_REPORTS:
        return None
    try:
        require_fresh(request)
    except FreshAuthenticationRequired:
        return step_up_response(
            reverse("admin:report_export", args=(request_id,)),
            admin_navigation.PAGES["report_export"].label,
        )
    return None


def _redirect(identifier):
    """Native form submission uses a private Post/Redirect/Get handoff."""
    response = redirect("admin:report_export", request_id=identifier)
    response["Cache-Control"] = "no-store"
    return response


def _error(
    request,
    *,
    campaign_id=None,
    request_id=None,
    status=409,
    busy=False,
    bound=False,
    expired=False,
):
    """Fixed-text recovery never reflects a submitted value or internal failure.

    ``bound`` explains a reused one-time form (``ExportRequestBound``) and
    ``expired`` an authorized download of an expired file (``ExportExpired``).
    """
    debug_swallowed("report request refused")
    # No request context processors: a database outage must not trigger another
    # database query while rendering its recovery response.
    response = HttpResponse(
        render_to_string(
            "stewardship/report-export-error.html",
            {
                "campaign_id": campaign_id,
                "request_id": request_id,
                "busy": busy,
                "bound": bound,
                "expired": expired,
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
    except ExportRequestBound:
        return _error(request, campaign_id=campaign_id, status=409, bound=True)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(request, campaign_id=campaign_id, status=503)
    except ValueError:
        return _error(request, campaign_id=campaign_id, status=400)


def _export_step(state):
    """The export flow step a request's state shows: preparing or download."""
    return "download" if state in {"ready", "expired"} else "prepare"


@require_GET
def detail(request, request_id):
    """Passive status never renews login; query/render remain guarded until close."""
    try:
        service = runtime()
        principal = _principal(
            request, service.store, read_only=True, ministry_jobs=True
        )
        if request.GET:
            raise ValueError("Export status has no query fields.")
        # Only the lock identity is loaded before the barrier.
        campaign_id = ExportRequest.objects.values_list("campaign_id", flat=True).get(
            pk=request_id
        )

        def fresh(guard):
            """Neither request UUID nor earlier session access bypasses revocation."""
            current = _principal(
                request, service.store, read_only=True, ministry_jobs=True
            )
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
                    "fact_set",
                    "configuration__parish",
                    "information_snapshot",
                    "directory_snapshot",
                    "ministry_snapshot",
                    "financial_snapshot",
                    "family_test_names_snapshot",
                )
                .defer(
                    "information_snapshot__document",
                    "directory_snapshot__document",
                    "ministry_snapshot__document",
                    "financial_snapshot__document",
                    "family_test_names_snapshot__document",
                )
                .annotate(
                    ministry_source_generation=F(
                        "ministry_snapshot__source__generation"
                    ),
                    information_source_generation=F(
                        "information_snapshot__source__generation"
                    ),
                    directory_source_generation=F(
                        "directory_snapshot__source__generation"
                    ),
                    financial_source_generation=F(
                        "financial_snapshot__source__generation"
                    ),
                )
                .get(pk=request_id)
            )
            state = export_status(service.store, principal.identity, request_id)
            mutable = True
            try:
                admit_campaign(campaign_id, mutating=True)
            except PermissionError:
                mutable = False
            # The report page the export came from, for the trail (#196).
            if job.report == "ministry":
                title = "Ministry export"
                source = "ministry_report"
                report_url = reverse("admin:ministry_report")
            elif job.report == "additional_information":
                title = "Additional-information export"
                source = "information_queue"
                report_url = reverse("admin:information_queue")
            elif job.report == "financial":
                title = "Financial stewardship export"
                source = "financial_report"
                report_url = reverse("admin:financial_report")
            elif job.report == "family_test_names":
                # Requested from the command line's chosen-Family review
                # (#817); the trail returns to that page for the same email.
                title = "Chosen-Family test names export"
                source = "campaign_mail_families"
                report_url = reverse(
                    "admin:campaign_mail_families", args=[job.parameters["revision"]]
                )
            elif job.report in {"family_directory", "postal_outreach"}:
                source = "family_directory"
                title = (
                    "Family-directory mail-merge export"
                    if job.parameters["postal"]
                    else "Family-directory export"
                )
                report_url = reverse("admin:family_directory")
                # Return with the closed link presets the export used: its reach
                # filter or, for a mail merge, the mailing columns alone. A mail
                # merge's reach is always "By postal mail only" (#951), which
                # the mailing columns apply themselves, so the page's own reach
                # stays Any for when the reader turns them off.
                reach = job.parameters["filters"].get("reach", "any")
                if job.parameters["postal"]:
                    presets = {"mailing": "yes"}
                else:
                    presets = {"reach": reach} if reach in REACH else {}
                if presets:
                    report_url += "?" + urlencode(presets)
            else:
                title = "Participation export"
                source = "participation"
                report_url = ReportQuery(
                    scope=job.parameters["population_scope"],
                    timezone=job.browser_timezone,
                ).url()
            admin_navigation.place(
                request,
                parent=source,
                flow="export",
                step=_export_step(state["state"]),
            )
            testing = (
                testing_codes_context(campaign_id, principal)
                if job.report in {"family_directory", "postal_outreach"}
                else {}
            )
            context = testing | {
                "job": job,
                "status": state,
                "mutable": mutable,
                "retry_key": uuid4(),
                "report_title": title,
                "report_url": report_url,
                # "Return to" names the report by its page name.
                "report_name": admin_navigation.PAGES[source].label,
                "can_cancel": state["state"] in {"queued", "running", "retry_wait"},
                # Why a still-queued export has not started (#340). Ministry
                # leaders may open their own exports but not background work,
                # so only a reader who may see that work learns what runs ahead.
                "wait": queue_wait(
                    TaskRun.objects.filter(root_id=job.task_id),
                    named=allows(principal, Capability.BACKGROUND_WORK),
                )
                if state["state"] == "queued"
                else None,
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
        principal = _principal(request, service.store, ministry_jobs=True)
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
            # A malformed key is refused before the export is even looked up.
            key = UUID(values["request_key"])
            refused = _regenerate_step_up(request, service.store, principal, request_id)
            if refused is not None:
                return refused
            result = regenerate_export(
                service.store, principal.identity, request_id, request_key=key
            )
            return _redirect(result.pk)
        else:
            raise ValueError("Unknown report action.")
        return _redirect(request_id)
    except (ExportConflict, TaskRetryConflict):
        return _error(request, request_id=request_id)
    except ExportRequestBound:
        # A stale Regenerate form on the status page; the shared notice's
        # "reload the page" means that page here, not the report.
        return _error(request, request_id=request_id, status=409, bound=True)
    except ExportExpired:
        # Only the authorized requester reaches this; tell them to regenerate
        # rather than showing a sign-in denial they cannot act on.
        return _error(request, request_id=request_id, status=410, expired=True)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(request, request_id=request_id, status=503)
    except ValueError:
        return _error(request, request_id=request_id, status=400)
