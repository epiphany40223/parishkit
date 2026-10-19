"""Admin/Staff Census changes worklist, with CSV, XLSX and PDF downloads (#528).

The page and its downloads follow the Limitations report
(``limitation_views``): filters, sorting and paging travel in CSRF-protected
POST bodies, so search text never reaches an address; the table pages and
sorts in place; a download is the filtered list rendered in memory, in the
timezone the page chose (this browser's); and every view and download is
audited as a count only.

The page is the census stream's review worklist (#1683; the shared
``components/review-worklist.html``): a row's Accept, Edit, Ignore, Back to
review or Entered by hand, and the bulk bar's, post to the page itself with
the page's filters and table choices (``review_worklist.read_decision``), so
the answer is the same page, refreshed in place, with a notice naming the
row. ``census_worklist`` builds the rows and records the decisions.
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
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin, database_now
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.pipeline import Stage
from parishkit.stewardship.responses.census_additions import AddRefusal
from parishkit.stewardship.responses.census_review import (
    InvalidEdit,
    KeyRebound,
    campaign_today,
)
from parishkit.stewardship.schema_primitives import timezone_names
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
from parishkit.stewardship.web.refusals import Refusal, with_codes, with_status_code
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.review_worklist import read_decision, record
from parishkit.stewardship.web.tables import Sorting, paginate, table_parameters

from . import census_publication
from .census_changes import (
    KINDS,
    ROUTES,
    STATUSES,
    CensusQuery,
    census_changes,
    census_csv,
    census_pdf,
    census_xlsx,
)
from .census_worklist import LINK_DEFAULTS, CensusDecisions, add_fields, worklist_page
from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .family_links import link_families
from .read_admission import admit_report_read

FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
ROUTE_LABELS = dict(
    zip(
        ROUTES,
        ("Automatic and by hand", "Automatic only", "By hand only"),
        strict=True,
    )
)
KIND_LABELS = dict(
    zip(
        KINDS,
        ("Every kind", "Contact details", "Moved", "Deceased", "New Member"),
        strict=True,
    )
)


# What a GET of the page may carry: the filters a link may (the view's own
# address, census_worklist.link_pairs) and the table's choices.
LINK_PARAMETERS = frozenset(LINK_DEFAULTS) | table_parameters()


def _text(value):
    """Case-insensitive text sort key; a missing value sorts as empty."""
    return (value or "").casefold()


# The list is complete in memory, so every column sorts here; the SQL order
# (Family DUID, then submission time) stays the tiebreak; the sort is stable.
SORTING = Sorting.by_column(
    {
        "family": lambda row: (_text(row["family_name"]), row["family_duid"]),
        "duid": lambda row: row["family_duid"],
        "who": lambda row: _text(row["who"]),
        # Members by DUID; New Members and household (Family) rows, which
        # have none, follow them.
        "member_duid": lambda row: (
            row["member_duid"] is None,
            row["member_duid"] or 0,
        ),
        "what": lambda row: _text(row["label"]),
        "status": lambda row: list(STATUSES).index(row["status"]),
        "submitted": lambda row: row["submitted_at"],
    },
    default="family",
    descending_first={"submitted"},
)


def paging_values(parameters):
    """Remove and return the table's page/size/sort values from the form.

    ``parameters`` is a mutable QueryDict. The values only choose what the
    screen shows, so they never reach the filters or a download.
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


def _principal(request, store, *, read_only=False, export=False):
    """Every request reloads current policy; Ministry leaders never qualify."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not allows(principal, Capability.VIEW_CENSUS) or (
        export and not allows(principal, Capability.REPORT_EXPORT)
    ):
        raise PermissionError("Census changes are unavailable.")
    return principal


def _error(campaign_id, *, status):
    """No private filter or exception values, and a way back to the page."""
    debug_swallowed("report request refused")
    response = HttpResponse(
        render_to_string(
            "stewardship/census-changes-error.html",
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


def _choices(parameters, *, export):
    """Parse the posted form: paging (page) or format and zone (download).

    The download's zone is the browser's, posted unasked, or an explicit
    ``timezone`` from an older tab (``dates.download_zone``, #1679).
    """
    extra = {}
    if export:
        fmt = parameters.pop("format", [DEFAULT_EXPORT_FORMAT])
        chosen = parameters.pop("timezone", [None])
        browser = parameters.pop("browser_timezone", [None])
        if len(fmt) != 1 or len(chosen) != 1 or len(browser) != 1:
            raise ValueError("Invalid census change export choice.")
        zone = dates.download_zone(chosen[0], browser[0])
        if fmt[0] not in FORMATS or (zone != "UTC" and zone not in timezone_names()):
            raise ValueError("Invalid census change export choice.")
        extra = {"format": fmt[0], "zone": ZoneInfo(zone)}
    else:
        extra["paging"] = paging_values(parameters)
    return extra


# A refused edit value's static reason, shown at its field in the dialog.
EDIT_REFUSED = "Correct the value, then save it again."
REBOUND = Refusal(
    "This page was already used to record different choices, so nothing was recorded.",
    "Reload the page, then try again.",
)


def _edit_refusal(error):
    """The page's answer to an edited value the change cannot take.

    Its static reason, shown in place at the editor's field (an address: at
    each part with a reason of its own) and logged by the shared refusal
    hook with the field's name only.
    """
    fields = [
        FieldError(ErrorCode.INVALID, f"value_{part}") for part in error.fields
    ] or [FieldError(ErrorCode.INVALID, "value")]
    return validation_response(fields, refusal=Refusal(str(error), EDIT_REFUSED))


def _add_refusal(error):
    """The page's answer to an Add, or a reopen, the reader can correct (#1710).

    Its static reason and next step, shown in the dialog at the fields it
    names (``AddRefusal.field_ids``), with its closed code for the refusal
    log.
    """
    fields = [FieldError(ErrorCode.INVALID, name) for name in error.field_ids]
    return with_codes(
        validation_response(fields, refusal=Refusal(str(error), error.fix)),
        error.code,
    )


def _respond(request, campaign_id, *, export, render):
    """Shared admission, purge protection, audit and failure handling.

    ``render(result, query, extra)`` returns the bytes. Both the page and a
    download read on the web connection under the interactive campaign read
    guard; a download is rendered in memory, not a stored export file, as
    the Limitations report's is (see ``limitation_views._respond``).
    """
    finish, handed_off = None, False
    action = Action.CENSUS_CHANGES_EXPORTED if export else Action.CENSUS_CHANGES_VIEWED
    try:
        service = runtime()
        principal = _principal(request, service.store, export=export)
        admit_report_read(campaign_id)
        try:
            if request.GET and (export or request.method != "GET"):
                raise ValueError("Census change filters require private POST state.")
            # A GET carries only the choices a link may (R38 finding 6): the
            # view's own address, which the page puts in the address bar, so
            # a reload draws the same view; the name search never does.
            if set(request.GET) - LINK_PARAMETERS:
                raise ValueError("Census change links carry no private filter.")
            parameters = (
                request.GET if request.method == "GET" else request.POST
            ).copy()
            parameters.pop("csrfmiddlewaretoken", None)
            decision = None if export else read_decision(parameters)
            extra = _choices(parameters, export=export)
            if not export and "stage" not in parameters:
                # The worklist opens on To review (review pipeline spec,
                # "Review worklist").
                parameters["stage"] = Stage.TO_REVIEW.value
            query = CensusQuery.parse(parameters)
            # Recorded in its own work transaction, before the page's read,
            # so the page that answers shows the result.
            if decision is not None:
                extra["notice"] = record(
                    CensusDecisions(service.store, campaign_id), principal, decision
                )
        except KeyRebound:
            # The same drawn form was sent again with other choices (its
            # first answer was lost): nothing was saved, and a reload draws
            # fresh forms with fresh keys.
            return validation_response(
                [FieldError(ErrorCode.STALE)], status=409, refusal=REBOUND
            )
        except AddRefusal as error:
            return _add_refusal(error)
        except InvalidEdit as error:
            return _edit_refusal(error)
        except ValueError:
            # A malformed form, or a decision the change's stage does not
            # offer: a 400, as a bad filter is.
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
            """A role change while the list is produced ends it."""
            nonlocal principal
            fresh = _principal(request, service.store, read_only=True, export=export)
            if fresh.identity != principal.identity:
                raise PermissionError("Census change access changed.")
            principal = fresh
            admit_report_read(campaign_id)

        def content():
            """Only detached authorized data reaches the template or file writer."""
            nonlocal count
            result = census_changes(campaign_id, query, principal)
            count = len(result["rows"])
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


@require_http_methods(["GET", "POST"])
def report(request, campaign_id):
    """The default GET shows the changes To review; filters use POST, and a
    GET may carry the view's own address (its link-safe choices, R38)."""

    def render(result, query, extra):
        """The census stream's review worklist: one table, redrawn in place."""
        action = reverse("admin:census_changes")
        table = paginate(
            result["rows"],
            extra["paging"],
            carry=list(query.form_values().items()),
            sorting=SORTING,
        )
        # Each shown Family name opens its Timeline (#1570): one read of
        # the page's Family record ids.
        link_families(campaign_id, table.rows)
        # Other campaign work (a restore review, cleanup, purge or go-live
        # step) holds the campaign: the page offers no actions, as the
        # services would refuse them, and says why.
        mutable = True
        try:
            admit_campaign(campaign_id, mutating=True)
        except PermissionError:
            mutable = False
        worklist, table = worklist_page(
            result, query, table, action, mutable=mutable, paging=extra["paging"]
        )
        return render_to_string(
            "stewardship/census-changes.html",
            {
                "worklist": worklist,
                "table": table,
                "campaign_id": campaign_id,
                "query": query,
                "query_fields": query.form_values(),
                "route_choices": ROUTE_LABELS.items(),
                "kind_choices": KIND_LABELS.items(),
                "notice": extra.get("notice"),
                # The latest date an edited birth or death date may hold:
                # the campaign's today, as the server checks it.
                "edit_today": campaign_today(
                    Campaign.objects.select_related("active_configuration").get(
                        pk=campaign_id
                    )
                ).isoformat(),
                "mutable": mutable,
                # The Add dialog's choices (#1710): what may change, and the
                # ignored changes an addition may be added in place of.
                "add_fields": add_fields(),
                "add_replaceable": result["replaceable"],
                "administrator": result["administrator"],
                # Publish to ParishSoft's link (#528, ADM-09), the
                # Administrator's: unavailable, with its reason, while the
                # deployment's parishsoft_publication setting is off. #1688
                # renames it Update ParishSoft.
                "publication_on": census_publication.enabled(),
            },
            request=request,
        ).encode()

    return _respond(request, campaign_id, export=False, render=render)


def export_file_name(fmt, now):
    """The download's file name, stamped with the UTC instant ``now``."""
    return f"stewardship-census-changes-{now.strftime('%Y%m%d-%H%M%SZ')}.{fmt}"


def export_body(campaign_id, result, fmt, zone):
    """The filtered list as the chosen file's bytes, times in ``zone``.

    The page's download and ``pk-admin export census`` (#463) both render
    through here, so their CSV, XLSX and PDF cannot drift. The PDF's page
    frame and the XLSX's "Report information" sheet (#926) also name the
    parish, campaign and download time; the PDF dates itself in the
    parish's date format, lent explicitly so the file never depends on the
    request's date middleware (as the response lists' downloads do). The
    CSV is only the table, so it reads none of them.
    """
    if fmt == "csv":
        return census_csv(result, zone)
    parish = (
        SystemConfiguration.objects.select_related("active_configuration__parish")
        .get()
        .active_configuration.parish
    )
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    context = {
        "parish": parish.name,
        "campaign": campaign.active_configuration.name,
        "as_of": database_now(),
    }
    if fmt == "xlsx":
        return census_xlsx(result, zone, **context)
    with dates.using(parish.date_format):
        return census_pdf(result, zone, **context)


@page_download("census_changes")
@require_http_methods(["POST"])
def export(request, campaign_id):
    """Download the filtered list as CSV, XLSX or PDF, rendered on request."""

    def render(result, query, extra):
        """The chosen file format, with times in the chosen timezone."""
        return export_body(campaign_id, result, extra["format"], extra["zone"])

    return _respond(request, campaign_id, export=True, render=render)
