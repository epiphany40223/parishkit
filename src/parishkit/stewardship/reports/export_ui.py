"""Accessible native controls over the compiled participation export services."""

from urllib.parse import urlencode
from uuid import UUID, uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from parishkit.stewardship.accounts import admin_navigation
from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queue_wait import queue_wait
from parishkit.stewardship.jobs.storage import TaskRetryConflict
from parishkit.stewardship.observability import debug_swallowed
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.dates import parish_zone, unasked_zone
from parishkit.stewardship.web.refusals import with_status_code
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
    export_ended,
    export_status,
    issue_download,
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
    request,
    *,
    campaign_id=None,
    request_id=None,
    status=409,
    busy=False,
    bound=False,
):
    """Fixed-text recovery never reflects a submitted value or internal failure.

    ``bound`` explains a reused one-time form (``ExportRequestBound``).
    An ended export answers ``gone_page`` instead.
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
                "temporary": status == 503,
            },
        ),
        status=status,
    )
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    if status == 503:
        response["Retry-After"] = "5"
    # The refusal log's codes for this page of its own (#1403).
    return with_status_code(
        response,
        *(["busy"] if busy else []),
        *(["form_reused"] if bound else []),
    )


@require_POST
def create(request, campaign_id):
    """Pin the generation shown in the preview, including an explicitly stale one."""
    try:
        service = runtime()
        principal = _principal(request, service.store)
        values = _body(
            request,
            {"fact_set_id", "format", "request_key"},
            optional={"browser_timezone"},
        )
        job = create_export(
            service.store,
            principal.identity,
            campaign_id=campaign_id,
            fact_set_id=UUID(values["fact_set_id"]),
            format=values["format"],
            # The page asks for no zone (#1127): the one it was drawn in,
            # else the parish's.
            browser_timezone=unasked_zone(
                values.get("browser_timezone"), parish_zone()
            ),
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


def _origin(job):
    """Where ``job`` came from: (title, source page, crumb arguments and
    queries, report URL).

    The status page uses it for its trail and "Return to", and the "no
    longer available" page for its link back to the report. It reads only
    the request row, never its capture.
    """
    # The report page the export came from, for the trail (#196),
    # and any route arguments and query that page's crumb needs. A
    # report's export is named after that report's page (#520), so
    # ``title`` stays None for those and is built from the page's
    # name below; other files have names of their own.
    title = None
    arguments = {}
    queries = {}
    if job.report == "ministry":
        source = "ministry_report"
        report_url = reverse("admin:ministry_report")
    elif job.report == "additional_information":
        source = "information_queue"
        report_url = reverse("admin:information_queue")
    elif job.report == "financial":
        source = "financial_report"
        report_url = reverse("admin:financial_report")
    elif job.report == "campaign_archive":
        # Download campaign archive on Past campaigns (#1018).
        title = _("Campaign archive")
        source = "past_campaigns"
        report_url = reverse("admin:past_campaigns")
    elif job.report == "campaign_comparison":
        title = _("Campaign comparison download")
        source = "campaign_comparison"
        report_url = reverse("admin:campaign_comparison")
    elif job.report == "family_test_names":
        # Requested from the command line's chosen-Family review
        # (#817). The trail runs through that page for the same email:
        # it and Preview and test email need the revision to link, so
        # the crumb is the way back and no "Return to" repeats it.
        title = _("Chosen-Family test names export")
        source = "campaign_mail_families"
        arguments = {"revision_id": job.parameters["revision"]}
        report_url = reverse(
            "admin:campaign_mail_families", args=[job.parameters["revision"]]
        )
    elif job.report == "family_timeline":
        # The export sits under the Family's own timeline page, so the
        # trail runs Directory > Family timeline > export and keeps
        # which Family it was (#521).
        from .family_timeline_views import timeline_url

        title = _("Family timeline export")
        source = "family_timeline"
        # From the request's parameters, not its capture, which is deleted
        # when the export ends (#1398).
        family_id = UUID(job.parameters["family_id"])
        arguments = {"family_id": family_id}
        report_url = timeline_url(family_id, job.parameters["mode"])
        # The crumb opens the timeline in the mode exported, so the
        # page draws no "Return to" link beside it (#1345).
        if "?" in report_url:
            queries = {source: report_url.split("?", 1)[1]}
    elif job.report in {"family_directory", "postal_outreach"}:
        source = "family_directory"
        if job.parameters["postal"]:
            title = _("%(page)s mail-merge export") % {
                "page": admin_navigation.PAGES[source].label
            }
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
        source = "participation"
        report_url = ReportQuery(
            scope=job.parameters["population_scope"],
            timezone=job.browser_timezone,
        ).url()
    if title is None:
        title = _("%(page)s export") % {"page": admin_navigation.PAGES[source].label}
    return title, source, arguments, queries, report_url


class ExportGone(Exception):
    """This authorized export has ended; answer the fixed 410 page.

    ``job`` is the request row the raiser already loaded, so the handler
    draws the page without a second read.
    """

    def __init__(self, job):
        super().__init__("This export has ended.")
        self.job = job


def gone_page(request, source, report_url, *, arguments=None, queries=None):
    """The fixed "no longer available" page (410 Gone) for an ended export.

    Exports are point-in-time (#1398): once an export's window is over, or
    it was cancelled, its old status page is not shown; this page links
    back to the report (``source``, at ``report_url``) to export it again.
    Callers check the export's owner and capability first, so the report it
    names is never revealed to anyone else.
    """
    admin_navigation.place(
        request, parent=source, arguments=arguments or {}, queries=queries or {}
    )
    response = HttpResponse(
        render_to_string(
            "stewardship/report-export-gone.html",
            {
                "report_url": report_url,
                "report_name": admin_navigation.PAGES[source].label,
            },
            request=request,
        ),
        status=410,
    )
    response["Cache-Control"] = "no-store"
    return response


def _gone(request, job):
    """``gone_page`` for a report export, from its request row alone."""
    _, source, arguments, queries, report_url = _origin(job)
    return gone_page(request, source, report_url, arguments=arguments, queries=queries)


def _refuse_ended(store, principal, request_id):
    """Raise ``ExportGone`` when this export has ended, after the owner check.

    Its Cancel, Retry and Download answer the fixed "no longer available"
    page once it has ended (#1398), whichever was clicked on an open page.
    """
    with transaction.atomic():
        export = ExportRequest.objects.get(pk=request_id)
        authorize(store, principal.identity, request=export)
        if export_ended(export):
            raise ExportGone(export)


def _export_step(state):
    """The export flow step a request's state shows: preparing or download."""
    return "download" if state == "ready" else "prepare"


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
            export = ExportRequest.objects.get(pk=request_id)
            authorize(service.store, current.identity, request=export)
            # Only after the owner and capability check (#1398).
            if export_ended(export):
                raise ExportGone(export)

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
                    "timeline_snapshot",
                    "comparison_snapshot",
                )
                .defer(
                    "information_snapshot__document",
                    "directory_snapshot__document",
                    "ministry_snapshot__document",
                    "financial_snapshot__document",
                    "family_test_names_snapshot__document",
                    "timeline_snapshot__document",
                    "comparison_snapshot__document",
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
            title, source, arguments, queries, report_url = _origin(job)
            admin_navigation.place(
                request,
                parent=source,
                arguments=arguments,
                queries=queries,
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
                # The trail's last crumb names the page as its heading does,
                # as the latest-data export's does (#520).
                "breadcrumb_label": title,
                "report_url": report_url,
                # "Return to" names the report by its page name. The page
                # draws it only when ``report_url`` differs from the parent
                # crumb's link, that is when it keeps the export's filters
                # (#1345).
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
    except ExportGone as error:
        return _gone(request, error.job)
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
        values = _body(request, {"request_key"} if action == "retry" else set())
        _refuse_ended(service.store, principal, request_id)
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
        else:
            raise ValueError("Unknown report action.")
        return _redirect(request_id)
    except (ExportConflict, TaskRetryConflict):
        return _error(request, request_id=request_id)
    except ExportGone as error:
        # Only the authorized requester reaches this (the owner check runs
        # first); tell them the export is gone rather than showing a
        # sign-in denial they cannot act on.
        return _gone(request, error.job)
    except ExportExpired:
        # The window ended between the check above and the download, so
        # the row is read once more, under the same outage handling.
        try:
            return _gone(request, ExportRequest.objects.get(pk=request_id))
        except (*SAFE_FAILURES, StorageInvariantError):
            return _error(request, request_id=request_id, status=503)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(request, request_id=request_id, status=503)
    except ValueError:
        return _error(request, request_id=request_id, status=400)
