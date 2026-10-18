"""Roster changes to enter: resolved joins and leaves for ParishSoft (#528, step 4).

The ParishSoft API cannot change Ministry rosters, so every join or leave a
person resolved (*joined* or *leave confirmed*, with no resolution source) is
entered in ParishSoft by hand. This Admin and Staff list shows those not yet
marked **Entered in ParishSoft** (or all of them), with the tick in place,
and downloads as CSV, XLSX or PDF with the page's columns: the file staff
work from. The stored Ministry change summary export is unchanged (the
Administrator's default, #528).

The rows come from the follow-up selection (``followup_page``, read whole)
plus one query for the Member and Family keys, one for the Family names and
one for the ticks, all in the same guarded read; filtering, sorting and
paging happen in memory, as the Census changes worklist's do. Nothing here
is private search text, so the list's choices are an ordinary GET table.
The view and each download are audited as a count only.
"""

import csv
import io
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, connection, transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_GET, require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin, database_now
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.source.snapshot_names import snapshot_family_names
from parishkit.stewardship.source.source_status import NAME_NOT_RECORDED
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.contracts import expected_version, filters
from parishkit.stewardship.web.exports import (
    DEFAULT_EXPORT_FORMAT,
    csv_cell,
    download_headers,
    page_download,
)
from parishkit.stewardship.web.refusals import with_status_code
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.tables import Sorting, paginate, table_parameters
from parishkit.stewardship.workflows.models import (
    MinistryRequest,
    MinistryWorkflowRevision,
)
from parishkit.stewardship.workflows.roster import (
    MAX_BULK,
    RosterStale,
    can_tick,
    eligible,
    mark,
    mark_many_entered,
    roster_marks,
)

from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .ministry_followup import FollowupQuery, followup_page, valid_ministry
from .read_admission import admit_report_read

FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
# "todo" (the default) is what staff still have to enter; "all" adds the ones
# already marked, so a mistaken mark can be found and cleared.
SHOW = {"todo": "Not yet entered", "all": "All, including entered"}
# The page's columns, in the shared order (UX conventions, "Table column
# order"): the Member leads (each row is one Member's change), the Member and
# Family names each with its DUID beside it, then the Ministry by name alone
# (no Ministry DUID, #1123), the change, when and by whom it was resolved, and
# its Entered in ParishSoft mark. The page adds an Actions column last.
HEADINGS = (
    "Member",
    "Member DUID",
    # Blank while ParishSoft lists the Member; otherwise #1216's phrase
    # (#1217), in its own column so the name stays a name.
    "ParishSoft status",
    "Family",
    "Family DUID",
    "Ministry",
    "Request",
    "Resolved",
    "Resolved by",
    "Replaced by a later response",
    "Entered in ParishSoft",
    "Entered by",
    "Entered at",
)
# Relative PDF column widths, in percent of the page's body width: a whole
# DUID, "Not yet" and "Leave" stay on one line, and a compact time keeps its
# date on one line and its time on the next; names and addresses wrap.
PDF_WEIGHTS = {
    "Member": 8,
    "Member DUID": 7,
    "ParishSoft status": 5,
    "Family": 10,
    "Family DUID": 7,
    "Ministry": 12,
    "Request": 6,
    "Resolved": 10,
    "Resolved by": 7,
    "Replaced by a later response": 5,
    "Entered in ParishSoft": 7,
    "Entered by": 6,
    "Entered at": 10,
}
PRIVACY = "Sensitive parish report. Share only with authorized recipients."
NEW_MEMBER = "New Member"
FORMER = "Former portal user"


def _text(value):
    """Case-insensitive text sort key; a missing value sorts as empty."""
    return (value or "").casefold()


def _number(value):
    """A DUID sort key: rows without one follow the numbered rows."""
    return (value is None, value or 0)


# The list is complete in memory, so every named column sorts here; the
# newest resolution comes first by default.
SORTING = Sorting.by_column(
    {
        "member": lambda row: _text(row["member_name"]),
        "member_duid": lambda row: _number(row["member_duid"]),
        "family": lambda row: (_text(row["family_name"]), row["family_duid"]),
        "duid": lambda row: row["family_duid"],
        "ministry": lambda row: (_text(row["ministry_name"]), row["ministry_duid"]),
        "request": lambda row: row["action"],
        "resolved": lambda row: row["resolved_at"],
    },
    default="-resolved",
    descending_first={"resolved"},
)


def _principal(request, store, *, read_only=False):
    """Admin and Staff only; a Ministry leader never sees this list."""
    principal = authenticated_admin(
        request, store=store, activity=not read_only, read_only=read_only
    )
    if not can_tick(principal):
        raise PermissionError("Roster changes are unavailable.")
    return principal


def roster_rows(campaign_id, principal, show, ministry=""):
    """Every resolved roster change, with its names, DUIDs, resolver and tick.

    Every request is read (``history="all"``), not only each Member and
    Ministry's newest: a Family that answers again creates a new request for
    a join or leave it still asks for, and the resolved one would otherwise
    vanish from the list before anyone entered it. Eligibility keeps only
    resolved roster outcomes with no resolution source, so a superseded or
    cancelled request never appears. A change for a Member ParishSoft no
    longer lists, or lists as inactive or deceased, stays listed, marked
    (``source_mark``): it may still need entering (#1217); the list is
    Admin and Staff only. ``ministry`` (a DUID as text, or "")
    keeps one Ministry's changes. Returns the shown rows and the selection's
    result (for its metadata and its Ministries).
    """
    rows, result = _ticked(campaign_id, principal)
    ids = [row["id"] for row in rows]
    families = {
        str(pk): family
        for pk, family in MinistryRequest.objects.filter(pk__in=ids).values_list(
            "pk", "submission__family__family_duid"
        )
    }
    resolvers = _resolvers(ids)
    for row in rows:
        row["family_duid"] = families.get(str(row["id"]))
        row["resolved_by"] = resolvers.get(str(row["id"]), "")
    names = _family_names({row["family_duid"] for row in rows})
    for row in rows:
        row["family_name"] = names.get(row["family_duid"], "")
    shown = [
        row
        for row in rows
        if (show == "all" or not row["roster_entered"])
        and (not ministry or str(row["ministry_duid"]) == ministry)
    ]
    return shown, result


def _ticked(campaign_id, principal):
    """The list's requests with their ticks, and the selection's result.

    Every request is read (``history="all"``) and eligibility keeps the
    resolved roster outcomes with no resolution source (``roster_rows``
    says why); each row then carries its current tick (``mark``). Shared by
    the page and the menu's count (admin_context.OPEN_COUNTS repeats this
    selection and eligibility in SQL), so the two select alike.
    """
    result = followup_page(
        campaign_id,
        FollowupQuery(state="resolved", history="all"),
        principal,
        whole=True,
        every_member=True,
    )
    rows = [row for row in result["rows"] if eligible(row)]
    marks = roster_marks([row["id"] for row in rows])
    return [mark(row, marks) for row in rows], result


def _family_names(duids):
    """Each Family's surname-and-heads name, from the current ParishSoft data.

    The shared helper names a Family as every Admin table does (#932), and
    one with no usable surname "Name not recorded in ParishSoft" (#1216).
    With no promoted data, or a compacted snapshot, the names are blank and
    the Family DUID still identifies the Family.
    """
    if not duids:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT snapshot_id FROM stewardship_source_current WHERE singleton"
        )
        found = cursor.fetchone()
    return snapshot_family_names(found[0], duids, NAME_NOT_RECORDED) if found else {}


def _resolvers(request_ids):
    """Who resolved each request: the latest revision that resolved it."""
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
    # A resolver whose portal user is gone is named as the other worklists
    # name one.
    return {key: emails.get(actor, FORMER) for key, actor in latest.items()}


def iso_time(moment):
    """A file's machine-readable time: ISO 8601 to the second, with offset."""
    return moment.isoformat(timespec="seconds")


def export_rows(rows, zone, *, time_text=iso_time):
    """The list as plain text, times placed in ``zone``.

    ``time_text`` words a time: ISO 8601 for CSV and XLSX, the parish's
    compact date format for the PDF. A New Member's DUID cell says so, as
    the page does.
    """

    def instant(value):
        """One time in the display zone, or blank."""
        return time_text(value.astimezone(zone)) if value else ""

    return [
        (
            row["member_name"],
            str(row["member_duid"]) if row["member_duid"] else NEW_MEMBER,
            row["source_mark"][0] if row.get("source_mark") else "",
            row["family_name"],
            str(row["family_duid"]) if row["family_duid"] else "",
            row["ministry_name"],
            "Join" if row["action"] == "join" else "Leave",
            instant(row["resolved_at"]),
            row["resolved_by"],
            # A Family answered again after this change was resolved.
            "Yes" if row.get("latest") is False else "",
            "Yes" if row["roster_entered"] else "Not yet",
            row["roster_by"] if row["roster_entered"] else "",
            instant(row["roster_at"]) if row["roster_entered"] else "",
        )
        for row in rows
    ]


def roster_csv(rows, zone):
    """One CSV with the page's columns."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADINGS)
    writer.writerows(
        [csv_cell(value) for value in row] for row in export_rows(rows, zone)
    )
    return buffer.getvalue().encode("utf-8")


def roster_xlsx(rows, zone):
    """A workbook with one literal-text sheet of the list."""
    # Imported here, like the other export writers, to keep web startup light.
    from .xlsx_design import table_workbook

    return table_workbook(
        HEADINGS,
        export_rows(rows, zone),
        sheet_title="Roster changes",
        freeze_first_column=True,
    )


def roster_pdf(rows, zone, *, parish, campaign, as_of):
    """The list as the shared framed zebra table (``pdf_design``).

    The table is ``HEADINGS`` over ``export_rows``, the CSV's and XLSX's
    columns and values, so the three files never disagree (#966); only the
    times read in the parish's compact date format. The caller lends the
    parish's date format (``dates.using``).
    """
    from .pdf_design import PdfFrame, Table, draw_table, table_pages

    count = len(rows)
    frame = PdfFrame(
        title="Roster changes to enter",
        eyebrow=f"{parish} · {campaign}",
        stamp=f"Downloaded {dates.display_text(as_of.astimezone(zone))} ({zone.key})",
        details=(f"{count:,} {'change' if count == 1 else 'changes'} in this file.",),
        notice=PRIVACY,
    )
    table = Table.weighted(HEADINGS, [PDF_WEIGHTS[name] for name in HEADINGS])
    lines = export_rows(
        rows, zone, time_text=lambda moment: dates.display_text(moment, compact=True)
    )
    pages = list(table_pages(table, lines, frame))
    output = io.BytesIO()
    frame.write(output, pages, len(pages), draw_table(table, frame), requested_at=as_of)
    return output.getvalue()


def _error(*, status):
    """No private values, and a way back to the list."""
    debug_swallowed("report request refused")
    response = HttpResponse(
        render_to_string("stewardship/ministry-roster-error.html", {"status": status}),
        status=status,
        headers={"Cache-Control": "no-store"},
    )
    response.stewardship_safe_error = True
    if status == 503:
        response["Retry-After"] = "5"
    # The refusal log's codes for this page of its own (#1403).
    return with_status_code(response)


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


def export_choice(fmt, zone_name):
    """The download's format and display zone, as ``{"format", "zone"}``.

    UTC or one supported timezone name, and one of ``FORMATS``; anything
    else is a ``ValueError`` (the page's 400, the command's ``invalid``).
    The page and ``pk-admin export roster`` both check through here.
    """
    if fmt not in FORMATS or (zone_name != "UTC" and zone_name not in timezone_names()):
        raise ValueError("Invalid roster export choice.")
    return {"format": fmt, "zone": ZoneInfo(zone_name)}


def export_file_name(fmt, now):
    """The download's file name, stamped with the UTC instant ``now``."""
    return f"stewardship-roster-changes-{now.strftime('%Y%m%d-%H%M%SZ')}.{fmt}"


def export_body(campaign_id, rows, fmt, zone):
    """The shown rows as the chosen file's bytes, times in ``zone``.

    The page's download and ``pk-admin export roster`` (#463) both render
    through here, so their CSV, XLSX and PDF cannot drift. The PDF names the
    parish and campaign in its page header and dates itself in the parish's
    date format, lent explicitly, as the Census changes download does.
    """
    if fmt != "pdf":
        writer = roster_xlsx if fmt == "xlsx" else roster_csv
        return writer(rows, zone)
    parish = (
        SystemConfiguration.objects.select_related("active_configuration__parish")
        .get()
        .active_configuration.parish
    )
    campaign = Campaign.objects.select_related("active_configuration").get(
        pk=campaign_id
    )
    with dates.using(parish.date_format):
        return roster_pdf(
            rows,
            zone,
            parish=parish.name,
            campaign=campaign.active_configuration.name,
            as_of=database_now(),
        )


def _choices(parameters, *, export):
    """The request's closed choices: Show, and paging or format and timezone.

    A page's GET also accepts ``changed=1``, the tick view's one-time answer
    that someone else changed a mark first.
    """
    allowed = {"show", "ministry"} | (
        {"format", "timezone", "csrfmiddlewaretoken"}
        if export
        else table_parameters() | {"changed"}
    )
    values = filters(parameters, allowed=allowed)
    show = values.pop("show", "todo")
    changed = values.pop("changed", None)
    ministry = values.pop("ministry", "")
    if (
        show not in SHOW
        or changed not in {None, "1"}
        or (ministry and not valid_ministry(ministry))
    ):
        raise ValueError("Invalid roster list choice.")
    if export:
        chosen = export_choice(
            values.get("format", DEFAULT_EXPORT_FORMAT), values.get("timezone", "UTC")
        )
        return show, chosen | {"ministry": ministry}
    # Refuse a bad sort, page or size now, as a 400, not while rendering.
    paginate([], values, sorting=SORTING)
    return show, {"paging": values, "changed": changed == "1", "ministry": ministry}


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
            show, extra = _choices(parameters, export=export)
        except ValueError:
            return _error(status=400)
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
            rows, result = roster_rows(campaign_id, principal, show, extra["ministry"])
            count = len(rows)
            return iter((render(rows, result, show, extra),))

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
            return _error(status=503)
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
        return _error(status=503)
    except ValueError as error:
        emit_failure(error, event=Event.REPORT_SHAPING_FAILED)
        return _error(status=503)
    finally:
        if finish is not None and not handed_off:
            finish(False)


def resolved_on(moment, zone):
    """A resolution's calendar day in the browser's ``zone``, compact style."""
    return dates.format_date(moment.astimezone(ZoneInfo(zone)).date(), compact=True)


def row_controls(rows):
    """Give each shown row what its tick form and selection box need.

    A fresh request key per drawn form makes a repeated submission a replay.
    ``label`` names the row as "Member — Ministry", in its icon button's
    accessible name and tooltip, its box's name and the bulk dialog's title
    (the follow-up queue's bulk outcomes name a row the same way). The same
    change can be listed twice (a Family answered again and both requests
    were resolved); those rows add the day they were resolved, "Member —
    Ministry, resolved Oct 5, 2054", so each control names its own row, as
    a stale bulk refusal does. That day is the viewer's own (#558), which
    the server cannot know on a GET: the label carries the resolution's UTC
    instant, and ``local_day`` (the same instant, the controls'
    ``data-local-day``) lets date-format-v1.js replace it with the compact
    day in the browser's time zone.
    """
    for row in rows:
        row["request_key"] = uuid4()
        row["local_day"] = None
        member = row["member_name"] or row["member_label"]
        row["label"] = f"{member} — {row['ministry_name']}"
    names = Counter(row["label"] for row in rows)
    for row in rows:
        if names[row["label"]] > 1 and row["resolved_at"]:
            row["local_day"] = row["resolved_at"].astimezone(UTC).isoformat()
            row["label"] += f", resolved {row['local_day']}"


def show_links(table, action):
    """Each Show segment's address (#1535): the list with that Show, the
    applied Ministry, sort and page size kept, back on its first page."""
    others = tuple(field for field in table.carried if field[0] != "show")
    return {
        key: f"{action}?{replace(table, carried=(('show', key), *others)).query(1)}"
        for key in SHOW
    }


@require_GET
def page(request, campaign_id):
    """The list, sorted and paged in place, with each row's tick."""

    def render(rows, result, show, extra):
        """The native page."""
        action = reverse("admin:ministry_roster")
        ministry = extra["ministry"]
        carry = [("show", show)] + ([("ministry", ministry)] if ministry else [])
        table = paginate(rows, extra["paging"], carry=carry, sorting=SORTING)
        mutable = True
        try:
            admit_campaign(campaign_id, mutating=True)
        except PermissionError:
            mutable = False
        row_controls(table.rows)
        tick_fields = table.page_fields(table.number)
        return render_to_string(
            "stewardship/ministry-roster.html",
            {
                "metadata": result["metadata"],
                "table": replace(table, action=action),
                "show": show,
                "show_links": show_links(table, action),
                "ministry": ministry,
                # The Ministry filter's choices: the Ministries the list reads.
                "ministries": result["ministries"],
                "changed": extra["changed"],
                "tick_fields": tick_fields,
                # The bulk dialog redraws the table from the list's own
                # address, with the same Show, Ministry, sort and page.
                "refresh_url": f"{action}?{table.current_query}",
                # One key per drawn page: each bulk entry's request key is
                # made from it, so a repeated post replays.
                "bulk_key": uuid4(),
                "mutable": mutable,
                "can_tick": True,
                "export_timezones": sorted(timezone_names()),
            },
            request=request,
        ).encode()

    return _respond(
        request, campaign_id, export=False, parameters=request.GET, render=render
    )


@page_download("ministry_roster")
@require_POST
def export(request, campaign_id):
    """Download the list as CSV, XLSX or PDF, rendered on request."""

    def render(rows, result, show, extra):
        """The chosen file format, with times in the chosen timezone."""
        return export_body(campaign_id, rows, extra["format"], extra["zone"])

    if request.GET:
        return _error(status=400)
    return _respond(
        request, campaign_id, export=True, parameters=request.POST, render=render
    )


# What a refused bulk mark says in its dialog (#879), naming the one change
# it is about by the names its row shows; nothing submitted is echoed.
BULK_STALE = _(
    "%(row)s changed after this list was shown (someone else may have marked "
    "it), so nothing was marked."
)


def bulk_values(parameters):
    """Strict closed grammar of the bulk bar's POST: the chosen ticks and key.

    Each ``request`` value is ``<request id>:<next sequence>``, as the row's
    box carries it: at most one page of them (``MAX_BULK``), none twice.
    ``bulk_key`` is the page's key, from which each entry's request key is
    made, so a repeated post replays. An optional single ``zone`` is the
    browser's time zone, read only to name a stale row's day (``bulk``).
    Anything else is a malformed form.
    """
    keys = set(parameters) - {"csrfmiddlewaretoken", "request", "zone"}
    values = parameters.getlist("request")
    if keys != {"bulk_key"} or len(parameters.getlist("bulk_key")) != 1:
        raise ValueError("Invalid roster bulk form.")
    if len(parameters.getlist("zone")) > 1:
        raise ValueError("Invalid roster bulk form.")
    if not 1 <= len(values) <= MAX_BULK:
        raise ValueError("Choose between one and a page of changes.")
    chosen = {}
    for value in values:
        identity, separator, sequence = value.partition(":")
        if not separator or len(value) > 64:
            raise ValueError("Invalid roster selection.")
        chosen[UUID(identity)] = expected_version(sequence)
    if len(chosen) != len(values):
        raise ValueError("A change was chosen twice.")
    return chosen, UUID(parameters["bulk_key"])


@require_POST
def bulk(request, campaign_id):
    """Mark the changes chosen on the list Entered in ParishSoft (#879).

    The shared confirmation dialog posts the ticked rows, each with its
    tick's next sequence, without a page load. All or none: a change someone
    else marked or cleared since the list was shown is named in the
    refusal the dialog shows (409); a malformed form, or a change that does
    not take the tick, is a 400; another campaign's request or a person who
    may not tick is denied. Success answers 204, and the dialog redraws the
    table from the list's own address, keeping its Show, Ministry, sort and
    page.
    """
    # Lazy, as the follow-up views read this module lazily too.
    from .ministry_followup_views import _bulk_refusal, _in_campaign, _row_name

    try:
        service = runtime()
        principal = _principal(request, service.store)
        if request.GET:
            raise ValueError("Roster marks require a POST body.")
        sequences, bulk_key = bulk_values(request.POST)
        _in_campaign(campaign_id, list(sequences))
        mark_many_entered(
            service.store, principal.identity, sequences, bulk_key=bulk_key
        )
        response = HttpResponse(status=204)
        response["Cache-Control"] = "no-store"
        return response
    except RosterStale as error:
        row = _row_name(campaign_id, principal, error.request_id)
        if row is None:
            return _error(status=409)
        # The change may be listed twice (row_controls), so the refusal
        # names the day it was resolved, which tells the rows apart, in the
        # browser's zone the dialog posts (#558). Without a known zone the
        # day is left out rather than shown in a guessed zone.
        zone = request.POST.get("zone", "")
        resolved = (
            MinistryRequest.objects.filter(pk=error.request_id)
            .values_list("resolved_at", flat=True)
            .first()
        )
        if resolved and zone in timezone_names():
            row += f", resolved {resolved_on(resolved, zone)}"
        return _bulk_refusal(409, BULK_STALE % {"row": row})
    except StaleRecordError:
        return _error(status=409)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(status=503)
    except ValueError:
        return _error(status=400)
