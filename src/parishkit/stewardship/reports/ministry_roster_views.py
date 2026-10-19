"""The roster changes to enter: resolved joins and leaves for ParishSoft (#528).

The ParishSoft API cannot change Ministry rosters, so every join or leave a
person accepted (*joined* or *leave confirmed*, with no resolution source) is
entered in ParishSoft by hand. Staff enter them from Ministry requests at its
Stage To enter by hand (admin-portal spec, "Roster entry on Ministry
requests"): the Roster changes to enter page that once listed them (#1660,
#1687) was folded into Ministry requests (R39 finding 2), and its address is
gone with no redirect. What stays here is that list's selection and its
file, which ``pk-admin report roster``, ``export roster`` and ``followup
roster-bulk`` still read (until #1688 renames the commands), and the
Family keys Ministry requests' roster rows and export borrow
(``family_keys``).

The rows come from the follow-up selection (``followup_page``, read whole)
plus one query for the Member and Family keys, one for the Family names and
one for the ticks, all in the same guarded read, and one for the file's
rows' last decisions. Each download is an export in the chosen zone, the
one its command names (#1679). Each read and download is audited as a
count only.
"""

import csv
import io
from zoneinfo import ZoneInfo

from django.db import connection, transaction

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.accounts.sessions import authenticated_admin, database_now
from parishkit.stewardship.audit.schemas import ActorKind
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.source.snapshot_names import snapshot_family_names
from parishkit.stewardship.source.source_status import NAME_NOT_RECORDED
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.exports import csv_cell
from parishkit.stewardship.workflows.models import MinistryRequest
from parishkit.stewardship.workflows.review import placement
from parishkit.stewardship.workflows.roster import (
    can_tick,
    eligible,
    mark,
    roster_marks,
)

from .ministry_followup import FollowupQuery, followup_page
from .ministry_worklist import _last_decisions, reviewer_value

FORMATS = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
# ``pk-admin report roster`` and ``export roster``'s ``--show`` (until #1688
# renames the commands): "todo" is what staff still have to enter, "all"
# adds the ones already marked.
SHOW = {"todo": "Not yet entered", "all": "All, including entered"}
# The files' columns: the retired page's, without Actions, its cells split
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
    not yet entered; ``all``: marked ones too). ``ministry`` (a DUID as
    text, or "") keeps one Ministry's changes. Each row carries its
    ``placement`` and ``stage`` (the pipeline's, #1687). Returns the shown
    rows and the selection's result (for its metadata and its Ministries).
    """
    rows, result = _ticked(campaign_id, principal)
    family_keys(rows)
    for row in rows:
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
    the command line and Home's to-enter count (admin_context.OPEN_COUNTS
    repeats this selection and eligibility in SQL), so the two select alike.
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


def family_keys(rows):
    """Give each follow-up row its Family DUID and Family name, in place.

    One read for the requests' Family DUIDs and one for their names
    (``_family_names``), for the rows given. Ministry requests' roster rows
    and export use it too, so both name a Family alike. Returns ``rows``.
    """
    families = {
        str(pk): family
        for pk, family in MinistryRequest.objects.filter(
            pk__in=[row["id"] for row in rows]
        ).values_list("pk", "submission__family__family_duid")
    }
    names = _family_names({families.get(str(row["id"])) for row in rows} - {None})
    for row in rows:
        row["family_duid"] = families.get(str(row["id"]))
        row["family_name"] = names.get(row["family_duid"], "")
    return rows


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

    One statement for the given rows (every row of a download), through
    the Ministry worklist's own read, so the file and Ministry requests word
    a request's last decision alike. Returns ``rows``.
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
    Ministry requests does. The rows carry their Last decision (``with_decisions``).
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
    else is a ``ValueError`` (the command's ``invalid``).
    ``pk-admin export roster`` checks through here.
    """
    if fmt not in FORMATS or (zone_name != "UTC" and zone_name not in timezone_names()):
        raise ValueError("Invalid roster export choice.")
    return {"format": fmt, "zone": ZoneInfo(zone_name)}


def export_file_name(fmt, now):
    """The download's file name, stamped with the UTC instant ``now``."""
    return f"stewardship-roster-changes-{now.strftime('%Y%m%d-%H%M%SZ')}.{fmt}"


def export_body(campaign_id, rows, fmt, zone):
    """The shown rows as the chosen file's bytes, times in ``zone``.

    ``pk-admin export roster`` (#463) renders through here, so its CSV,
    XLSX and PDF cannot drift. Each row's Last
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
