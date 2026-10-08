"""Roster changes to enter: resolved joins and leaves for ParishSoft (#528, step 4).

The ParishSoft API cannot change Ministry rosters, so every join or leave a
person resolved (*joined* or *leave confirmed*, with no resolution source) is
entered in ParishSoft by hand. This Admin and Staff list shows those not yet
marked **Entered in ParishSoft** (or all of them), with the tick in place,
and downloads as CSV or XLSX: the file staff work from. The stored Ministry
change summary export is unchanged (the Administrator's default, #528).

The rows come from the follow-up selection (``followup_page``, read whole)
plus one query for the Member and Family DUIDs and one for the ticks, all in
the same guarded read; filtering, sorting and paging happen in memory, as
the talents report's do. Nothing here is private search text, so the list's
choices are an ordinary GET table. The view and each download are audited
as a count only.
"""

import csv
import io
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.exports import csv_cell, download_headers
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.tables import Sorting, paginate, table_parameters
from parishkit.stewardship.workflows.models import MinistryRequest
from parishkit.stewardship.workflows.roster import (
    can_tick,
    eligible,
    mark,
    roster_marks,
)

from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .information_rendering import xlsx_cell
from .ministry_followup import FollowupQuery, followup_page
from .read_admission import admit_report_read

FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
# "todo" (the default) is what staff still have to enter; "all" adds the ones
# already marked, so a mistaken mark can be found and cleared.
SHOW = {"todo": "Not yet entered", "all": "All, including entered"}
HEADINGS = (
    "Member",
    "Member DUID",
    "Family DUID",
    "Ministry",
    "Ministry DUID",
    "Request",
    "Resolved",
    "Resolved by",
    "Entered in ParishSoft",
    "Entered by",
    "Entered at",
)


def _text(value):
    """Case-insensitive text sort key; a missing value sorts as empty."""
    return (value or "").casefold()


SORTING = Sorting.by_column(
    {
        "member": lambda row: _text(row["member_name"]),
        "ministry": lambda row: (_text(row["ministry_name"]), row["ministry_duid"]),
        "request": lambda row: row["action"],
        "resolved": lambda row: row["resolved_at"],
    },
    default="-resolved",
    descending_first={"resolved"},
)


def _principal(request, store, *, read_only=False, export=False):
    """Admin and Staff only; a Ministry leader never sees this list."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not can_tick(principal):
        raise PermissionError("Roster changes are unavailable.")
    return principal


def roster_rows(campaign_id, principal, show):
    """Every resolved roster change, with its DUIDs, resolver and tick, as rows.

    Every request is read (``history="all"``), not only each Member and
    Ministry's newest: a Family that answers again creates a new request for
    a join or leave it still asks for, and the resolved one would otherwise
    vanish from the list before anyone entered it (#821 review). Eligibility
    keeps only resolved roster outcomes with no resolution source, so a
    superseded or cancelled request never appears.
    """
    result = followup_page(
        campaign_id,
        FollowupQuery(state="resolved", history="all"),
        principal,
        whole=True,
    )
    rows = [row for row in result["rows"] if eligible(row)]
    resolvers = _resolvers([row["id"] for row in rows])
    identities = {
        str(pk): (kind, key, family)
        for pk, kind, key, family in MinistryRequest.objects.filter(
            pk__in=[row["id"] for row in rows]
        ).values_list(
            "pk", "entity_kind", "entity_key", "submission__family__family_duid"
        )
    }
    marks = roster_marks(row["id"] for row in rows)
    for row in rows:
        mark(row, marks)
        kind, key, family = identities.get(str(row["id"]), ("", "", ""))
        row["member_duid"] = key if kind == "member" else ""
        row["family_duid"] = family
        row["resolved_by"] = resolvers.get(str(row["id"]), "")
    shown = [row for row in rows if show == "all" or not row["roster_entered"]]
    return shown, result


def _resolvers(request_ids):
    """Who resolved each request: the latest revision that resolved it."""
    from parishkit.stewardship.accounts.policy_models import PortalUser
    from parishkit.stewardship.workflows.models import MinistryWorkflowRevision

    latest = {}
    for request_id, actor_id in (
        MinistryWorkflowRevision.objects.filter(
            request_id__in=request_ids, state="resolved"
        )
        .order_by("request_id", "-expected_version")
        .values_list("request_id", "actor_id")
    ):
        latest.setdefault(str(request_id), actor_id)
    emails = dict(
        PortalUser.objects.filter(pk__in=set(latest.values())).values_list(
            "pk", "email"
        )
    )
    return {key: emails.get(actor, "") for key, actor in latest.items()}


def export_rows(rows, zone):
    """The list as plain text, times in the requested timezone."""

    def instant(value):
        """One instant in the display zone, or blank."""
        return value.astimezone(zone).isoformat(timespec="seconds") if value else ""

    return [
        (
            row["member_name"],
            row["member_duid"],
            str(row["family_duid"]),
            row["ministry_name"],
            str(row["ministry_duid"]),
            "Join" if row["action"] == "join" else "Leave",
            instant(row["resolved_at"]),
            row["resolved_by"],
            "Yes" if row["roster_entered"] else "",
            row["roster_by"] if row["roster_entered"] else "",
            instant(row["roster_at"]) if row["roster_entered"] else "",
        )
        for row in rows
    ]


def roster_csv(rows, zone):
    """One CSV with the list's columns."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADINGS)
    writer.writerows(
        [csv_cell(value) for value in row] for row in export_rows(rows, zone)
    )
    return buffer.getvalue().encode("utf-8")


def roster_xlsx(rows, zone):
    """A workbook with one literal-text sheet of the list."""
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Roster changes"
    for number, values in enumerate((HEADINGS, *export_rows(rows, zone)), 1):
        for column, value in enumerate(values, start=1):
            xlsx_cell(sheet, number, column, value)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def _error(campaign_id, *, status):
    """No private values, and a way back to the list."""
    debug_swallowed("report request refused")
    response = HttpResponse(
        render_to_string(
            "stewardship/ministry-roster-error.html",
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
    """Retain access evidence as a count only."""
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


def _respond(request, campaign_id, *, export, parameters, render):
    """Shared admission, guarded read, audit and failure handling.

    ``render(rows, result, show, extra)`` returns the bytes; ``parameters``
    are the request's choices (GET for the page, POST for a download).
    """
    finish, handed_off = None, False
    action = Action.ROSTER_CHANGES_EXPORTED if export else Action.ROSTER_CHANGES_VIEWED
    try:
        service = runtime()
        principal = _principal(request, service.store)
        admit_report_read(campaign_id)
        try:
            allowed = {"show"} | (
                {"format", "timezone", "csrfmiddlewaretoken"}
                if export
                else table_parameters() | {"changed"}
            )
            values = filters(parameters, allowed=allowed)
            show = values.pop("show", "todo")
            # A tick someone else changed first (the tick view's answer).
            changed = values.pop("changed", None)
            if show not in SHOW or changed not in {None, "1"}:
                raise ValueError("Invalid roster list choice.")
            extra = {}
            if export:
                fmt, zone = values.get("format", "csv"), values.get("timezone", "UTC")
                if fmt not in FORMATS or (
                    zone != "UTC" and zone not in timezone_names()
                ):
                    raise ValueError("Invalid roster export choice.")
                extra = {"format": fmt, "zone": ZoneInfo(zone)}
            else:
                values.pop("csrfmiddlewaretoken", None)
                paginate([], values, sorting=SORTING)
                extra = {"paging": values, "changed": changed == "1"}
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
            fresh = _principal(request, service.store, read_only=True)
            if fresh.identity != principal.identity:
                raise PermissionError("Roster list access changed.")
            principal = fresh
            admit_report_read(campaign_id)

        def content():
            """Only detached authorized data reaches the template or file."""
            nonlocal count
            rows, result = roster_rows(campaign_id, principal, show)
            count = len(rows)
            return iter((render(rows, result, show, extra),))

        response = campaign_response(
            request,
            [campaign_id],
            authorize=authorize,
            open_content=content,
            on_close=finish,
            **({"content_type": FORMATS[extra["format"]]} if export else {}),
        )
        if response.status_code == 503 and not response.streaming:
            return _error(campaign_id, status=503)
        handed_off = response.status_code == 200 and response.streaming
        if export and handed_off:
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
            for name, value in download_headers(
                f"stewardship-roster-changes-{stamp}.{extra['format']}",
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


@require_GET
def page(request, campaign_id):
    """The list, sorted and paged in place, with each row's tick."""

    def render(rows, result, show, extra):
        """The native page."""
        table = paginate(rows, extra["paging"], carry=[("show", show)], sorting=SORTING)
        mutable = True
        try:
            admit_campaign(campaign_id, mutating=True)
        except PermissionError:
            mutable = False
        # A fresh key per drawn tick form makes a repeat a replay.
        for row in table.rows:
            row["request_key"] = uuid4()
        return render_to_string(
            "stewardship/ministry-roster.html",
            {
                "metadata": result["metadata"],
                "table": table,
                "campaign_id": campaign_id,
                "show": show,
                "show_choices": SHOW.items(),
                "changed": extra["changed"],
                "tick_fields": table.page_fields(table.number),
                "mutable": mutable,
                "can_tick": True,
                "export_timezones": sorted(timezone_names()),
            },
            request=request,
        ).encode()

    return _respond(
        request, campaign_id, export=False, parameters=request.GET, render=render
    )


@require_POST
def export(request, campaign_id):
    """Download the list as CSV or XLSX, rendered on request."""

    def render(rows, result, show, extra):
        """The chosen file format, with times in the chosen timezone."""
        writer = roster_xlsx if extra["format"] == "xlsx" else roster_csv
        return writer(rows, extra["zone"])

    if request.GET:
        return _error(campaign_id, status=400)
    return _respond(
        request, campaign_id, export=True, parameters=request.POST, render=render
    )
