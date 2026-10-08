"""Admin/Staff Census changes worklist, with CSV and XLSX downloads (#528).

The page and its downloads follow the Talents and limitations report
(``talent_views``): filters, sorting and paging travel in CSRF-protected
POST bodies, so search text never reaches an address; the table pages and
sorts in place; a download is the filtered list rendered in memory, in the
timezone the page chose (this browser's); and every view and download is
audited as a count only.
"""

from dataclasses import replace
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
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.exports import download_headers
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.tables import Sorting, paginate, table_parameters

from .census_changes import (
    KINDS,
    ROUTES,
    STATUS_CHOICES,
    STATUSES,
    CensusQuery,
    census_changes,
    census_csv,
    census_xlsx,
)
from .export_views import SAFE_FAILURES
from .read_admission import admit_report_read

FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
# The filter's choices, in order, with their plain labels.
STATUS_LABELS = {
    "open": "Work to do",
    **{key: STATUSES[key] for key in STATUS_CHOICES if key in STATUSES},
    "all": "Every status",
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


def _text(value):
    """Case-insensitive text sort key; a missing value sorts as empty."""
    return (value or "").casefold()


# The list is complete in memory, so every column sorts here; the SQL order
# (Family DUID, then submission time) stays the tiebreak; the sort is stable.
SORTING = Sorting.by_column(
    {
        "family": lambda row: (_text(row["family_name"]), row["family_duid"]),
        "who": lambda row: _text(row["who"]),
        "what": lambda row: _text(row["label"]),
        # Automatic first, then By hand, whatever the labels say.
        "route": lambda row: not row["automatic"],
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


def _choices(parameters, *, export):
    """Parse the posted form: paging (page) or format and timezone (download)."""
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
            raise ValueError("Invalid census change export choice.")
        extra = {"format": fmt[0], "zone": ZoneInfo(zone[0])}
    else:
        extra["paging"] = paging_values(parameters)
    return extra


def _respond(request, campaign_id, *, export, render):
    """Shared admission, purge protection, audit and failure handling.

    ``render(result, query, extra)`` returns the bytes. Both the page and a
    download read on the web connection under the interactive campaign read
    guard; a download is rendered in memory, not a stored export file, as
    the talents report's is (see ``talent_views._respond``).
    """
    finish, handed_off = None, False
    action = Action.CENSUS_CHANGES_EXPORTED if export else Action.CENSUS_CHANGES_VIEWED
    try:
        service = runtime()
        principal = _principal(request, service.store, export=export)
        admit_report_read(campaign_id)
        try:
            if request.GET:
                raise ValueError("Census change filters require private POST state.")
            parameters = request.POST.copy()
            parameters.pop("csrfmiddlewaretoken", None)
            extra = _choices(parameters, export=export)
            query = CensusQuery.parse(parameters)
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
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
            for name, value in download_headers(
                f"stewardship-census-changes-{stamp}.{extra['format']}",
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
    """The default GET shows the work to do; filters use POST."""

    def render(result, query, extra):
        """The native page: one table that pages and sorts in place."""
        action = reverse("admin:census_changes", args=[campaign_id])
        table = paginate(
            result["rows"],
            extra["paging"],
            carry=list(query.form_values().items()),
            sorting=SORTING,
        )
        return render_to_string(
            "stewardship/census-changes.html",
            result
            | {
                "table": replace(table, method="post", action=action),
                "campaign_id": campaign_id,
                "query": query,
                "query_fields": query.form_values(),
                "status_choices": STATUS_LABELS.items(),
                "route_choices": ROUTE_LABELS.items(),
                "kind_choices": KIND_LABELS.items(),
                "export_timezones": sorted(timezone_names()),
            },
            request=request,
        ).encode()

    return _respond(request, campaign_id, export=False, render=render)


@require_http_methods(["POST"])
def export(request, campaign_id):
    """Download the filtered list as CSV or XLSX, rendered on request."""

    def render(result, query, extra):
        """The chosen file format, with times in the chosen timezone."""
        if extra["format"] == "xlsx":
            return census_xlsx(result, extra["zone"])
        return census_csv(result, extra["zone"])

    return _respond(request, campaign_id, export=True, render=render)
