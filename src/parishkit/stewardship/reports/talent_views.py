"""Admin/Staff talents and limitations report, with CSV and XLSX downloads (#247)."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID
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
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.exports import download_headers
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.tables import Sorting, paginate, table_parameters

from .export_views import SAFE_FAILURES
from .read_admission import admit_report_read
from .response_lists import current_snapshot
from .talents import TalentQuery, talents_csv, talents_report, talents_xlsx

# The page's two tables page and sort independently, so their table
# parameters carry these prefixes.
MEMBERS, FAMILIES = "members_", "families_"


def _text(value):
    """Case-insensitive text sort key; a missing value sorts as empty."""
    return (value or "").casefold()


# The report is complete in memory (the selection does not page it), so every
# column sorts here, and the SQL order stays the tiebreak (Python's sort is
# stable). Times sort newest first on their first click.
MEMBER_SORTING = Sorting.by_column(
    {
        "member": lambda row: _text(row["member_name"]),
        # A Member the Family added on the form has no DUID yet: last (#960).
        "member_duid": lambda row: row["member_duid"],
        "family": lambda row: (_text(row["family_name"]), row["family_duid"]),
        "talents": lambda row: _text("; ".join(row["talents"])),
        "cannot_serve": lambda row: bool(row["cannot_serve"]),
        "latest": lambda row: row["submitted_at"],
    },
    default="family",
    descending_first={"cannot_serve", "latest"},
)
FAMILY_SORTING = Sorting.by_column(
    {
        "family": lambda row: _text(row["family_name"]),
        "duid": lambda row: row["family_duid"],
        "latest": lambda row: row["submitted_at"],
    },
    default="family",
    descending_first={"latest"},
)


def paging_values(parameters):
    """Remove and return both tables' page/size/sort values from the form.

    ``parameters`` is a mutable QueryDict. The values only choose what the
    screen shows, so they never reach the SQL filters or a download; each
    must be a single value that ``paginate`` accepts.
    """
    values = {}
    for name in table_parameters(MEMBERS) | table_parameters(FAMILIES):
        found = parameters.pop(name, None)
        if found is None:
            continue
        if len(found) != 1:
            raise ValueError("Table choices must be single values.")
        values[name] = found[0]
    # Refuse a bad choice now, as a 400, not later while rendering the page.
    for prefix, sorting in ((MEMBERS, MEMBER_SORTING), (FAMILIES, FAMILY_SORTING)):
        paginate([], values, prefix=prefix, sorting=sorting)
    return values


def tables(result, query, paging, action):
    """Sort and page both tables as private POST tables (web/tables.py).

    Each table's navigator and headings carry the private filters and the
    other table's current choices, so paging one never resets the other.
    """
    filters = list(query.form_values().items())
    shown = {}
    for prefix, rows, sorting in (
        (MEMBERS, result["members"], MEMBER_SORTING),
        (FAMILIES, result["families"], FAMILY_SORTING),
    ):
        other = [
            (key, value) for key, value in paging.items() if not key.startswith(prefix)
        ]
        table = paginate(
            rows, paging, prefix=prefix, carry=filters + other, sorting=sorting
        )
        shown[prefix] = replace(table, method="post", action=action)
    return shown[MEMBERS], shown[FAMILIES]


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


# The Talents filter's own words; any other ``talent`` value is a configured
# talent option's UUID (talents.OPTION).
TALENT_WORDS = frozenset({"any", "cannot_serve", "cannot_attend"})


def audit_choices(query, snapshot):
    """The audit context naming what was read, without the search text (#556).

    The talent choice is a closed word, or ``option`` with the configured
    talent's UUID (a parish setting). The search text can name a Family, so
    only whether one was used is kept. ``snapshot`` is the ParishSoft data the
    names came from, or None when it is unknown.
    """
    context = {"search_used": bool(query.search)}
    if query.talent in TALENT_WORDS:
        context["report_filter"] = query.talent
    else:
        context["report_filter"] = "option"
        context["talent_option_id"] = UUID(query.talent)
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
    ``extra`` holds the format and display timezone chosen on the page.

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
                raise ValueError("Talent filters require private POST state.")
            parameters = request.POST.copy()
            parameters.pop("csrfmiddlewaretoken", None)
            extra = {}
            if not export:
                extra["paging"] = paging_values(parameters)
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
        # What the audit records: the parsed choices until the report reads
        # them as offered, and the snapshot once the read has named it.
        finalized, count, audited, snapshot = False, 0, query, None

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
                    audit_choices(audited, snapshot),
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
            nonlocal count, audited, snapshot
            campaign = Campaign.objects.select_related("active_configuration").get(
                pk=campaign_id
            )
            configuration = campaign.active_configuration.values
            # A removed talent filter reads as "Everything" for the tables,
            # the re-posted sort/paging forms and the download alike.
            shown = audited = query.offered(configuration)
            # The report's SQL reads the current snapshot itself, and this
            # read-committed guard could see a refresh promote between
            # statements, so the audit names the snapshot only when it was
            # the same before and after the report.
            before = current_snapshot()
            result = talents_report(
                campaign_id, shown, principal, configuration=configuration
            )
            if current_snapshot() == before:
                snapshot = before
            count = len(result["members"]) + len(result["families"])
            return iter((render(result, shown, extra),))

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
                f"stewardship-talents-{stamp}.{extra['format']}",
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
    """Default GET shows everything; search and talent filters use POST."""

    def render(result, query, extra):
        """The native report page."""
        members, families = tables(
            result,
            query,
            extra["paging"],
            reverse("admin:talents_report"),
        )
        return render_to_string(
            "stewardship/talents-report.html",
            result
            | {
                "members_table": members,
                "families_table": families,
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
