"""Roster changes to enter: resolved joins and leaves for ParishSoft (#528, step 4).

The ParishSoft API cannot change Ministry rosters, so every join or leave a
person accepted (*joined* or *leave confirmed*, with no resolution source) is
entered in ParishSoft by hand. This Admin and Staff list is the Ministry
stream's manual handling report (#1687, review pipeline spec, "Manual
handling reports"): the shared review worklist (``web/review_worklist``)
with Stage (To enter by hand by default, or Entered by hand) and Ministry
as its always-open filters, offering only Entered by hand, one row or in
bulk, recorded through ``workflows.roster.RosterDecisions``. It downloads
as CSV, XLSX or PDF with the page's columns, through the shared export
buttons below the table: the file staff work from. The stored Ministry
change summary export is unchanged (the Administrator's default, #528).

The rows come from the follow-up selection (``followup_page``, read whole)
plus one query for the Member and Family keys, one for the Family names and
one for the ticks, all in the same guarded read, and one for the shown rows'
last decisions; filtering, sorting and paging happen in memory, as the
Census changes worklist's do. The worklist's controls post its view to the
page itself, which answers with the page redrawn in place. The view and
each download are audited as a count only.
"""

import csv
import io
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, connection, transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_http_methods, require_POST

from parishkit.stewardship.accounts.authentication import denial, runtime
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin, database_now
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.observability import Event, debug_swallowed, emit_failure
from parishkit.stewardship.pipeline import Action as Step
from parishkit.stewardship.pipeline import Stage, Stream
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.source.snapshot_names import snapshot_family_names
from parishkit.stewardship.source.source_status import NAME_NOT_RECORDED
from parishkit.stewardship.storage import StorageInvariantError
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.exports import (
    DEFAULT_EXPORT_FORMAT,
    csv_cell,
    download_headers,
    page_download,
)
from parishkit.stewardship.web.refusals import with_status_code
from parishkit.stewardship.web.responses import campaign_response
from parishkit.stewardship.web.review_worklist import (
    Column,
    build,
    read_decision,
    record,
)
from parishkit.stewardship.web.tables import Sorting, paginate, table_parameters
from parishkit.stewardship.workflows.models import MinistryRequest
from parishkit.stewardship.workflows.review import placement, reader_roles
from parishkit.stewardship.workflows.roster import (
    RosterDecisions,
    can_tick,
    eligible,
    mark,
    roster_marks,
)

from .export_services import admit_campaign
from .export_views import SAFE_FAILURES
from .family_links import link_families
from .ministry_followup import FollowupQuery, followup_page, valid_ministry
from .ministry_worklist import (
    _item_url,
    _last_decisions,
    _member_cell,
    _time,
    reviewer_value,
)
from .read_admission import admit_report_read

FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
# ``pk-admin report roster`` and ``export roster``'s ``--show`` (until #1688
# renames the commands): "todo" is what staff still have to enter, "all"
# adds the ones already marked.
SHOW = {"todo": "Not yet entered", "all": "All, including entered"}
# The page's Stage choices (#1687), To enter by hand first and the default;
# they replaced the Show pair (Administrator decision, 2026-10-18). Not seen
# in ParishSoft joins them with #1689.
STAGES = (Stage.TO_ENTER_BY_HAND, Stage.ENTERED_BY_HAND)
EVERY = "all"
STAGE_VALUES = frozenset({*(stage.value for stage in STAGES), EVERY})
DEFAULT_STAGE = Stage.TO_ENTER_BY_HAND.value
PREFIX = "roster"
# The worklist's identity columns, in the shared order (UX conventions,
# "Table column order"): the Member leads (each row is one Member's change),
# the Member and Family names each with its DUID beside it, then the
# Ministry to enter by name alone (no Ministry DUID, #1123). The worklist
# adds Family's answer, Reviewer's value, Stage, Submitted, Last decision and
# Actions.
COLUMNS = (
    Column("member_cell", "Member", sort="member", link="item_url"),
    Column("duid_cell", "Member DUID", sort="member_duid", numeric=True),
    Column("family", "Family", sort="family"),
    Column("family_duid_cell", "Family DUID", sort="duid", numeric=True),
    Column("ministry_name", "Ministry", sort="ministry"),
)
# The files' columns: the page's, without Actions, its combined cells split
# into their parts (UX conventions, "Downloaded files"): the Member cell's
# ParishSoft status badge, the Stage cell's lines and the Last decision's
# who, when and note.
HEADINGS = (
    "Member",
    "Member DUID",
    # Blank while ParishSoft lists the Member; otherwise #1216's phrase
    # (#1217), the Member cell's badge.
    "ParishSoft status",
    "Family",
    "Family DUID",
    "Ministry",
    "Family's answer",
    "Reviewer's value",
    "Stage",
    # The decision the Stage cell's line names (Accepted, Accepted
    # (edited), Added by the parish).
    "Decision",
    "Replaced by a later response",
    "Entered by",
    "Entered at",
    "Submitted",
    "Last decision",
    "Last decision at",
    "Note",
)
# Relative PDF column widths, in percent of the page's body width: a whole
# DUID stays on one line, and a compact time keeps its date on one line and
# its time on the next; names, emails and notes wrap.
PDF_WEIGHTS = {
    "Member": 5,
    "Member DUID": 7,
    "ParishSoft status": 4,
    "Family": 5,
    "Family DUID": 7,
    "Ministry": 5,
    "Family's answer": 4,
    "Reviewer's value": 5,
    "Stage": 5,
    "Decision": 5,
    "Replaced by a later response": 4,
    "Entered by": 5,
    "Entered at": 10,
    "Submitted": 10,
    "Last decision": 5,
    "Last decision at": 10,
    "Note": 4,
}
PRIVACY = "Sensitive parish report. Share only with authorized recipients."
NEW_MEMBER = "New Member"
REPLACED = "Replaced by a later response"


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
    """Every accepted roster change, with its names, DUIDs, stage and tick.

    Every request is read (``history="all"``), not only each Member and
    Ministry's newest: a Family that answers again creates a new request for
    a join or leave it still asks for, and the accepted one would otherwise
    vanish from the list before anyone entered it. Eligibility keeps only
    resolved roster outcomes with no resolution source, so a superseded or
    cancelled request never appears. A change for a Member ParishSoft no
    longer lists, or lists as inactive or deceased, stays listed, marked
    (``source_mark``): it may still need entering (#1217); the list is
    Admin and Staff only. ``show`` is the command line's choice (``todo``:
    not yet entered; ``all``); the page reads ``all`` and chooses its Stage
    itself. ``ministry`` (a DUID as text, or "") keeps one Ministry's
    changes. Each row carries its ``placement`` and ``stage`` (the
    pipeline's, #1687). Returns the shown rows and the selection's result
    (for its metadata and its Ministries).
    """
    rows, result = _ticked(campaign_id, principal)
    ids = [row["id"] for row in rows]
    families = {
        str(pk): family
        for pk, family in MinistryRequest.objects.filter(pk__in=ids).values_list(
            "pk", "submission__family__family_duid"
        )
    }
    for row in rows:
        row["family_duid"] = families.get(str(row["id"]))
    names = _family_names({row["family_duid"] for row in rows})
    for row in rows:
        row["family_name"] = names.get(row["family_duid"], "")
        row["family_ministry_name"] = row["ministry_name"]
        # The Ministry to enter is the one carried forward: an Edit's, in
        # place of the Family's (#1684).
        if row.get("reviewed_ministry_duid"):
            row["ministry_duid"] = row["reviewed_ministry_duid"]
            row["ministry_name"] = row["reviewed_ministry_name"]
        row["placement"] = placement(
            state=row["state"],
            outcome=row["outcome"],
            source_resolved=row["source_resolved"],
            roster_entered=row["roster_entered"],
            reviewed=bool(row.get("reviewed_ministry_duid")),
            added=bool(row.get("parish_added")),
        )
        row["stage"] = row["placement"].stage.value
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


def with_decisions(rows):
    """Give each row its Last decision (who, when, note), in place.

    One statement for the given rows (the page's shown page, or every row
    of a download), through the Ministry worklist's own read, so both
    pages word a request's last decision alike. Returns ``rows``.
    """
    decisions = _last_decisions(rows)
    for row in rows:
        row["decided_by"], row["decided_at"], row["note"] = decisions.get(
            str(row["id"]), ("", None, "")
        )
    return rows


def family_answer(row):
    """The Family's answer: Join or Leave, naming the Family's own Ministry
    when an Edit carried another forward; blank for a request the parish
    added, which no Family asked for."""
    if row.get("parish_added"):
        return ""
    kind = "Join" if row["action"] == "join" else "Leave"
    if row.get("reviewed_ministry_duid"):
        return f"{kind} {row['family_ministry_name']}"
    return kind


def iso_time(moment):
    """A file's machine-readable time: ISO 8601 to the second, with offset."""
    return moment.isoformat(timespec="seconds")


def export_rows(rows, zone, *, time_text=iso_time):
    """The list as plain text, times placed in ``zone``.

    ``time_text`` words a time: ISO 8601 for CSV and XLSX, the parish's
    compact date format for the PDF. A New Member's DUID cell says so, as
    the page does. The rows carry their Last decision (``with_decisions``).
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
            family_answer(row),
            reviewer_value(row),
            row["placement"].stage.label,
            row["placement"].decision.label if row["placement"].decision else "",
            # A Family answered again after this change was decided.
            "Yes" if row.get("latest") is False else "",
            row["roster_by"] if row["roster_entered"] else "",
            instant(row["roster_at"]) if row["roster_entered"] else "",
            instant(row["submitted_at"]),
            row["decided_by"],
            instant(row["decided_at"]),
            row["note"],
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
    through here, so their CSV, XLSX and PDF cannot drift. Each row's Last
    decision is read here (``with_decisions``). The PDF names the parish
    and campaign in its page header and dates itself in the parish's date
    format, lent explicitly, as the Census changes download does.
    """
    with_decisions(rows)
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
    """The request's closed choices: Stage, Ministry, and paging or format.

    The page's view is its Stage (To enter by hand by default) and
    Ministry, with the table's sort, page and size; a download's is the
    same Stage and Ministry, with its format and the browser's zone.
    Anything else, or a repeated value, is a ValueError (a 400).
    """
    allowed = {"stage", "ministry"} | (
        {"format", "timezone", "browser_timezone", "csrfmiddlewaretoken"}
        if export
        else table_parameters()
    )
    values = filters(parameters, allowed=allowed)
    stage = values.pop("stage", DEFAULT_STAGE)
    ministry = values.pop("ministry", "")
    if stage not in STAGE_VALUES or (ministry and not valid_ministry(ministry)):
        raise ValueError("Invalid roster list choice.")
    if export:
        # The browser's zone, posted unasked, or an explicit timezone from
        # an older tab (#1679).
        chosen = export_choice(
            values.get("format", DEFAULT_EXPORT_FORMAT),
            dates.download_zone(values.get("timezone"), values.get("browser_timezone")),
        )
        return stage, chosen | {"ministry": ministry}
    # Refuse a bad sort, page or size now, as a 400, not while rendering.
    paginate([], values, sorting=SORTING)
    return stage, {"paging": values, "ministry": ministry}


def at_stage(rows, stage):
    """The rows at the chosen Stage (Every stage keeps every row)."""
    return [row for row in rows if stage == EVERY or row["stage"] == stage]


def _respond(request, campaign_id, *, export, parameters, render):
    """Shared admission, guarded read, audit and failure handling.

    ``render(rows, result, stage, extra, principal)`` returns the bytes;
    ``rows`` are every change under the applied Ministry, at every stage;
    ``parameters`` are the request's choices (the query string or the POST
    body's view for the page, the POST body for a download).
    """
    finish, handed_off = None, False
    action = Action.ROSTER_CHANGES_EXPORTED if export else Action.ROSTER_CHANGES_VIEWED
    try:
        service = runtime()
        principal = _principal(request, service.store)
        admit_report_read(campaign_id)
        try:
            stage, extra = _choices(parameters, export=export)
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
            rows, result = roster_rows(campaign_id, principal, "all", extra["ministry"])
            count = len(at_stage(rows, stage))
            return iter((render(rows, result, stage, extra, principal),))

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


def _stage_line(row, decision):
    """The line under a row's Stage, as safe HTML (or "").

    The decision the stage word does not already say (Accepted (edited),
    Added by the parish), "Replaced by a later response" when the Family
    answered again after the change was decided (it still has to be
    entered), and who marked an entered change and when.
    """
    parts = [format_html("{}", decision)] if decision else []
    if row.get("latest") is False:
        parts.append(format_html("{}", REPLACED))
    if row["roster_entered"]:
        by = format_html("Entered by {}", row["roster_by"] or "Former portal user")
        parts.append(
            format_html("{}, {}", by, _time(row["roster_at"]))
            if row["roster_at"]
            else by
        )
    return format_html_join(mark_safe("<br>"), "{}", ((part,) for part in parts))


def row_label(row, ministry):
    """How the list names a change: "Member — Ministry", where ``ministry``
    is the Ministry to enter. A change the Family answered again after it
    was decided adds ", replaced by a later response": the same change can
    then be listed twice, and each control, dialog and notice names its own
    row (``decorate``, ``roster_name``)."""
    label = f"{row['member_name'] or row['member_label']} — {ministry}"
    if row.get("latest") is False:
        label += f", {REPLACED.lower()}"
    return label


def decorate(rows):
    """Give each shown row what the worklist component reads.

    ``name`` is how its icon, box and dialog name the row (``row_label``).
    Its version is the tick's next sequence, so a tick someone else changed
    first is stale (``RosterDecisions``). A Family name shown only when
    ParishSoft's data names it links its Timeline (#1570); with no name the
    cell stays blank and the Family DUID identifies the Family.
    """
    decorated = []
    for row in rows:
        label = row_label(row, row["ministry_name"])
        decorated.append(
            {
                "id": str(row["id"]),
                "version": row["roster_sequence"] + 1,
                "name": label,
                "placement": row["placement"],
                "member_cell": _member_cell(row),
                "item_url": _item_url(row, ""),
                "duid_cell": str(row["member_duid"])
                if row["member_duid"]
                else NEW_MEMBER,
                "family": row["family_name"],
                "family_name": row["family_name"],
                "family_id": row.get("family_id") if row["family_name"] else None,
                "family_duid_cell": str(row["family_duid"])
                if row["family_duid"]
                else "",
                "ministry_name": row["ministry_name"],
                "baseline": "",
                "family_answer": family_answer(row),
                "reviewer_value": reviewer_value(row),
                "parishsoft_now": "",
                "submitted_at": row["submitted_at"],
                "decided_by": row["decided_by"],
                "decided_at": row["decided_at"],
                "note": row["note"],
                "_source": row,
            }
        )
    return decorated


def stage_table(rows, stage, extra, *, action):
    """The chosen Stage's table page, and every Stage's count.

    ``rows`` are every change under the applied Ministry (``roster_rows``);
    the Stage choices count them all, and the table holds the chosen
    Stage's, sorted and paged in memory, its controls posting the view to
    ``action``.
    """
    counts = Counter(row["stage"] for row in rows)
    ministry = extra["ministry"]
    table = paginate(
        at_stage(rows, stage),
        extra["paging"],
        carry=[("stage", stage), ("ministry", ministry)],
        sorting=SORTING,
    )
    table = replace(table, method="post", action=action)
    return table, counts


def page_context(rows, result, stage, extra, roles, *, mutable, notice, prepare):
    """The page's template context for the applied view.

    ``rows`` are every change under the applied Ministry; ``roles`` the
    reader's pipeline roles, or none while other campaign work holds the
    list (``mutable`` False): every row is then drawn with no action and
    the selection bar is gone, and the page's notice says why.
    ``prepare(table_rows)`` gives the shown page's rows their Family record
    ids and Last decision (the view's reads; a fixture's own values).
    """
    action = reverse("admin:ministry_roster")
    table, counts = stage_table(rows, stage, extra, action=action)
    prepare(table.rows)
    worklist = build(
        Stream.MINISTRY,
        decorate(table.rows),
        roles,
        prefix=PREFIX,
        action=action,
        columns=COLUMNS,
        stage=stage,
        counts=counts,
        stages=STAGES,
        items="changes",
        caption="Roster changes to enter",
        baseline=False,
        fields={"ministry": extra["ministry"], "page": str(table.number)},
        # The manual handling report offers its mark alone, which records
        # no note (spec, "Manual handling reports").
        only={Step.ENTERED_BY_HAND},
        notes=False,
        filters_template="stewardship/ministry-roster-filters.html",
        export_template="stewardship/ministry-roster-export.html",
        empty_template="stewardship/ministry-roster-empty.html",
    )
    for row in worklist.rows:
        row["decision_label"] = _stage_line(row.pop("_source"), row["decision_label"])
    # The component draws the table's rows: the decorated ones.
    table = replace(table, rows=worklist.rows)
    return {
        "metadata": result["metadata"],
        "worklist": worklist,
        "table": table,
        "notice": notice,
        "ministry": extra["ministry"],
        # The Ministry filter's choices: the Ministries the list reads.
        "ministries": result["ministries"],
        # The export's hidden filters: the applied Stage and Ministry.
        "export_fields": {"stage": stage, "ministry": extra["ministry"]},
        "mutable": mutable,
    }


def _render_page(request, campaign_id, rows, result, stage, extra, principal, notice):
    """The native page: the worklist over the applied view, and its notice."""

    def prepare(shown):
        """Each shown Family name opens its Timeline (#1570), and each shown
        row names its last decision: one read each, for the page's rows."""
        link_families(campaign_id, shown)
        with_decisions(shown)

    mutable = True
    try:
        admit_campaign(campaign_id, mutating=True)
    except PermissionError:
        mutable = False
    context = page_context(
        rows,
        result,
        stage,
        extra,
        reader_roles(principal) if mutable else frozenset(),
        mutable=mutable,
        notice=notice,
        prepare=prepare,
    )
    return render_to_string(
        "stewardship/ministry-roster.html", context, request=request
    ).encode()


def roster_name(campaign_id, principal, request_id):
    """A request as its row on this list names it (``row_label``), or None.

    The Ministry is the one to enter, an Edit's in place of the Family's,
    as the row shows it. Read through the scoped selection, so only a
    request the person may see is named; any failure falls back to a
    notice that names no row.
    """
    try:
        with transaction.atomic():
            row = followup_page(
                campaign_id,
                FollowupQuery(state="any", history="all"),
                principal,
                request_id=request_id,
                every_member=True,
            )["rows"][0]
    except (DatabaseError, *SAFE_FAILURES, StorageInvariantError, IndexError):
        return None
    return row_label(row, row.get("reviewed_ministry_name") or row["ministry_name"])


def _decide(request, campaign_id, decision):
    """Record one Entered by hand decision; its notice, or the refusal.

    A race is the notice's to say (``record``). A request of another
    campaign, or a reader who may not tick, is denied like any unknown one;
    a malformed decision is the plain 400 page; an outage is 503.
    """
    # Lazy, as the follow-up views read this module lazily too.
    from .ministry_followup_views import _in_campaign

    try:
        service = runtime()
        principal = _principal(request, service.store)
        _in_campaign(campaign_id, [UUID(key) for key in decision.versions])
        decisions = RosterDecisions(
            service.store,
            namer=lambda request_id: roster_name(campaign_id, principal, request_id),
        )
        return record(decisions, principal, decision)
    except (PermissionError, ObjectDoesNotExist):
        return denial()
    except (*SAFE_FAILURES, StorageInvariantError):
        return _error(status=503)
    except ValueError:
        return _error(status=400)


@require_http_methods(["GET", "POST"])
def page(request, campaign_id):
    """The list, filtered, sorted and paged in place, with each row's mark.

    A GET is the default view (or the one its query string names). The
    worklist's controls post its view to the page itself; a POST carrying
    an Entered by hand decision (a row's icon or the bulk bar) records it
    first and answers with the page redrawn from the same view, its notice
    saying what was recorded (``web.review_worklist``).
    """
    notice = None
    if request.method == "POST":
        if request.GET:
            return _error(status=400)
        parameters = request.POST.copy()
        parameters.pop("csrfmiddlewaretoken", None)
        try:
            decision = read_decision(parameters)
            # The view the answer is drawn from is checked before anything
            # is recorded, so a malformed post records nothing.
            _choices(parameters, export=False)
        except ValueError:
            return _error(status=400)
        if decision is not None:
            notice = _decide(request, campaign_id, decision)
            if not isinstance(notice, str):
                return notice
    else:
        parameters = request.GET

    def render(rows, result, stage, extra, principal):
        """The page for the applied view."""
        return _render_page(
            request, campaign_id, rows, result, stage, extra, principal, notice
        )

    return _respond(
        request, campaign_id, export=False, parameters=parameters, render=render
    )


@page_download("ministry_roster")
@require_POST
def export(request, campaign_id):
    """Download the list as CSV, XLSX or PDF, rendered on request."""

    def render(rows, result, stage, extra, principal):
        """The chosen file format, with times in the chosen timezone."""
        return export_body(
            campaign_id, at_stage(rows, stage), extra["format"], extra["zone"]
        )

    if request.GET:
        return _error(status=400)
    return _respond(
        request, campaign_id, export=True, parameters=request.POST, render=render
    )
