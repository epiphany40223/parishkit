"""Admin/Staff Limitations worklist, with CSV, XLSX and PDF downloads (#247, #970).

The report was "Talents and limitations" until talents were retired (#970);
it keeps that report's audit actions (``talents_report_viewed`` and
``talents_report_exported``), so old and new System logs rows read alike.

The page is the cannot-participate stream's review worklist (#1685; the
shared ``components/review-worklist.html``): a row's Accept, Ignore, Back
to review or Handled, and the bulk bar's, post to the page itself with the
page's filters and table choices (``review_worklist.read_decision``), so
the answer is the same page, refreshed in place, with a notice naming the
answer. ``limitation_worklist`` builds the rows and records the decisions.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.role_refusal import require
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin, database_now
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.pipeline import Stage
from parishkit.stewardship.responses.census_review import KeyRebound
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.source.snapshot_names import current_snapshot
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.contracts import (
    ErrorCode,
    FieldError,
    validation_response,
)
from parishkit.stewardship.web.exports import (
    DEFAULT_EXPORT_FORMAT,
    download_headers,
    page_download,
)
from parishkit.stewardship.web.refusals import with_status_code
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.review_worklist import (
    EVERY_STAGE,
    REBOUND,
    read_decision,
    record,
)
from parishkit.stewardship.web.tables import paginate, table_parameters

from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .family_links import link_families
from .limitation_worklist import (
    SORTING,
    LimitationDecisions,
    WorklistQuery,
    answer_rows,
    at_stage,
    worklist_page,
)
from .limitations import (
    STAGES,
    LimitationsQuery,
    limitations_csv,
    limitations_pdf,
    limitations_report,
    limitations_xlsx,
    row_count,
)
from .limitations import at_stage as file_at_stage
from .read_admission import admit_report_read

# The download's Stage values: the worklist's own choices.
FILE_STAGES = frozenset({*(stage.value for stage in STAGES), EVERY_STAGE})


def paging_values(parameters):
    """Remove and return the table's page/size/sort values from the form.

    ``parameters`` is a mutable QueryDict. The values only choose what the
    screen shows, so they never reach the SQL filters or a download; each
    must be a single value that ``paginate`` accepts.
    """
    values = {}
    for name in table_parameters():
        found = parameters.pop(name, None)
        if found is None:
            continue
        if len(found) != 1:
            raise ValueError("Table choices must be single values.")
        values[name] = found[0]
    # Refuse a bad choice now, as a 400, not later while rendering the page.
    paginate([], values, sorting=SORTING)
    return values


FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


def export_choice(fmt, zone_name):
    """The download's format and display zone, as ``{"format", "zone"}``.

    UTC or one supported timezone name, and one of ``FORMATS``; anything
    else is a ``ValueError`` (the page's 400, the command's ``invalid``).
    The page and ``pk-admin export limitations`` both check through here.
    """
    if fmt not in FORMATS or (zone_name != "UTC" and zone_name not in timezone_names()):
        raise ValueError("Invalid Limitations export choice.")
    return {"format": fmt, "zone": ZoneInfo(zone_name)}


def _principal(request, store, *, read_only=False, export=False):
    """Every request reloads current policy; Ministry leaders never qualify."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    require(principal, Capability.CAMPAIGN_REPORT)
    if export and not allows(principal, Capability.REPORT_EXPORT):
        raise PermissionError("The Limitations report is unavailable.")
    return principal


def _error(campaign_id, *, status):
    """No private filter or exception values, and a way back to the report."""
    debug_swallowed("report request refused")
    response = HttpResponse(
        render_to_string(
            "stewardship/limitations-report-error.html",
            {"campaign_id": campaign_id, "status": status},
        ),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    if status == 503:
        response["Retry-After"] = "5"
    # The refusal log's codes for this page of its own (#1403).
    return with_status_code(response)


def audit_choices(query, snapshot):
    """The audit context naming what was read, without the search text (#556).

    The Show choice is a closed word. The search text can name a Family, so
    only whether one was used is kept. ``snapshot`` is the ParishSoft data the
    names came from, or None when it is unknown. (Rows recorded before #970
    may also hold ``option`` and ``talent_option_id`` for a talent filter.)
    """
    context = {"search_used": bool(query.search), "report_filter": query.show}
    if snapshot is not None:
        context["snapshot_id"] = snapshot
    return context


def _audit(action, principal, campaign_id, outcome, count, choices):
    """Retain access evidence: a count and ``audit_choices``, never a name."""
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
            context={"outcome": outcome, "count": count} | choices,
        )


def _respond(request, campaign_id, *, export, render):
    """Shared admission, purge protection, audit and failure handling.

    ``render(result, query, extra)`` returns the bytes; for a download,
    ``extra`` holds the format, display timezone and Stage chosen on the
    page; for the page, its table choices, its worklist filters and the
    notice of the decision it posted, which is recorded first, in its own
    work transaction, so the page that answers shows its result.

    Both the page and a download read on the web connection under the
    interactive campaign read guard. A download is not a stored export file:
    it is the report rendered in memory, as the System logs download is, so
    it does not use the dedicated download pool, whose login
    (``DOWNLOAD_READ_TABLES``) cannot read the campaign and source tables the
    report needs (#557). Its response carries the shared download headers.
    """
    finish, handed_off = None, False
    action = Action.TALENTS_REPORT_EXPORTED if export else Action.TALENTS_REPORT_VIEWED
    try:
        service = runtime()
        principal = _principal(request, service.store, export=export)
        admit_report_read(campaign_id)
        try:
            if request.GET:
                raise ValueError("Limitations filters require private POST state.")
            parameters = request.POST.copy()
            parameters.pop("csrfmiddlewaretoken", None)
            extra = {}
            if export:
                fmt = parameters.pop("format", [DEFAULT_EXPORT_FORMAT])
                # The browser's zone, posted unasked, or an explicit
                # timezone from an older tab (#1679).
                chosen = parameters.pop("timezone", [None])
                browser = parameters.pop("browser_timezone", [None])
                stage = parameters.pop("stage", [EVERY_STAGE])
                if (
                    len(fmt) != 1
                    or len(chosen) != 1
                    or len(browser) != 1
                    or len(stage) != 1
                    or stage[0] not in FILE_STAGES
                ):
                    raise ValueError("Invalid Limitations export choice.")
                extra = export_choice(
                    fmt[0], dates.download_zone(chosen[0], browser[0])
                ) | {"stage": stage[0]}
                query = LimitationsQuery.parse(parameters)
            else:
                decision = read_decision(parameters)
                extra["paging"] = paging_values(parameters)
                if "stage" not in parameters:
                    # The worklist opens on To review (review pipeline spec,
                    # "Review worklist").
                    parameters["stage"] = Stage.TO_REVIEW.value
                extra["worklist"] = WorklistQuery.parse(parameters)
                query = extra["worklist"].limitations
                if decision is not None:
                    extra["notice"] = record(
                        LimitationDecisions(service.store, campaign_id),
                        principal,
                        decision,
                    )
        except KeyRebound:
            # The same drawn form was sent again with other choices (its
            # first answer was lost): nothing was recorded, and a reload
            # draws fresh forms with fresh keys.
            return validation_response(
                [FieldError(ErrorCode.STALE)], status=409, refusal=REBOUND
            )
        except ValueError:
            # A malformed form, or a decision the answer does not take (a
            # Handled undo with no note): a 400, nothing recorded.
            return _error(campaign_id, status=400)
        # What the audit records: the parsed choices, and the snapshot once
        # the read has named it.
        finalized, count, snapshot = False, 0, None

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
                    audit_choices(query, snapshot),
                )
            except (DatabaseError, StorageInvariantError) as error:
                emit_failure(error, event=Event.REPORT_AUDIT_FAILED)

        def authorize(guard):
            """A role change while the report is produced ends it."""
            nonlocal principal
            fresh = _principal(request, service.store, read_only=True, export=export)
            if fresh.identity != principal.identity:
                raise PermissionError("Limitations report access changed.")
            principal = fresh
            admit_report_read(campaign_id)

        def content():
            """Only detached authorized data reaches the template or file writer."""
            nonlocal count, snapshot
            # A campaign that no longer exists is refused (ObjectDoesNotExist).
            Campaign.objects.only("pk").get(pk=campaign_id)
            # The report's SQL reads the current snapshot itself, and this
            # read-committed guard could see a refresh promote between
            # statements, so the audit names the snapshot only when it was
            # the same before and after the report.
            before = current_snapshot()
            result = limitations_report(campaign_id, query, principal)
            if current_snapshot() == before:
                snapshot = before
            count = row_count(result)
            return iter((render(result, query, extra),))

        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
            # No filename: that would route the read through the download pool.
            **({"content_type": FORMATS[extra["format"]]} if export else {}),
        )
        if response.status_code == 503 and not response.streaming:
            return _error(campaign_id, status=503)
        handed_off = response.status_code == 200 and response.streaming
        if export and handed_off:
            for name, value in download_headers(
                export_file_name(extra["format"], datetime.now(UTC)),
                content_type=FORMATS[extra["format"]],
            ).items():
                response[name] = value
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


def export_file_name(fmt, now):
    """The download's file name, stamped with the UTC instant ``now``."""
    return f"stewardship-limitations-{now.strftime('%Y%m%d-%H%M%SZ')}.{fmt}"


def export_body(campaign_id, result, fmt, zone):
    """The filtered report as the chosen file's bytes, times in ``zone``.

    The page's download and ``pk-admin export limitations`` (#463) both
    render through here, so their CSV, XLSX and PDF cannot drift. The PDF's
    page header and the XLSX's "Report information" sheet also name the
    parish, campaign and download time; the PDF dates itself in the
    parish's date format, lent explicitly, as the Census changes download
    does. The CSV is only the two tables, so it reads none of them.
    """
    if fmt == "csv":
        return limitations_csv(result, zone)
    parish = (
        SystemConfiguration.objects.select_related("active_configuration__parish")
        .get()
        .active_configuration.parish
    )
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    context = dict(
        parish=parish.name,
        campaign=campaign.active_configuration.name,
        as_of=database_now(),
    )
    if fmt == "xlsx":
        return limitations_xlsx(result, zone, **context)
    with dates.using(parish.date_format):
        return limitations_pdf(result, zone, **context)


@require_http_methods(["GET", "POST"])
def report(request, campaign_id):
    """The default GET shows the answers To review; filters and decisions POST."""

    def render(result, query, extra):
        """The stream's review worklist: one table, redrawn in place."""
        action = reverse("admin:limitations_report")
        worklist_query = extra["worklist"]
        shown, counts = at_stage(answer_rows(result), worklist_query.stage)
        table = paginate(
            shown,
            extra["paging"],
            carry=list(worklist_query.form_values().items()),
            sorting=SORTING,
        )
        # Each shown Family name opens its Timeline (#1570): one read of the
        # page's Family record ids.
        link_families(campaign_id, table.rows)
        # Other campaign work holds the campaign: the page offers no actions,
        # as the service would refuse them, and says why.
        mutable = True
        try:
            admit_campaign(campaign_id, mutating=True)
        except PermissionError:
            mutable = False
        worklist, table = worklist_page(
            counts, worklist_query, table, action, mutable=mutable
        )
        return render_to_string(
            "stewardship/limitations-report.html",
            {
                "worklist": worklist,
                "table": table,
                "campaign_id": campaign_id,
                "query": worklist_query,
                "query_fields": worklist_query.form_values(),
                "notice": extra.get("notice"),
                "mutable": mutable,
            },
            request=request,
        ).encode()

    return _respond(request, campaign_id, export=False, render=render)


@page_download("limitations")
@require_http_methods(["POST"])
def export(request, campaign_id):
    """Download the filtered report as CSV, XLSX or PDF, rendered on request."""

    def render(result, query, extra):
        """The chosen file format, with times in the chosen timezone.

        The file lists the answers at the page's Stage (#1685), as its
        table does.
        """
        return export_body(
            campaign_id,
            file_at_stage(result, extra["stage"]),
            extra["format"],
            extra["zone"],
        )

    return _respond(request, campaign_id, export=True, render=render)
