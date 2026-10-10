"""Census changes: the worklist of reported census changes for ParishSoft (#528).

Every census change a Family reported becomes a proposed-change row (one per
atomic request, ``responses/proposals.py``). This report reads those rows for
one campaign's live responses, as they are, and words them for the Admin and
Staff who carry them into ParishSoft, by hand or by publication: who and what
changed, ParishSoft's value now, the Family's answer and any Administrator
edit, how the change reaches ParishSoft, and a status derived from the row's
decision and execution (reports spec, "Census change rows"). It adds no state
of its own and changes nothing.

The rows are read with one parameterised query on the web login, which may
read the proposal, submission and source tables (``responses/grants.py``);
filtering, sorting and wording happen here, in memory, as the talents report
does, so they are plain functions a unit test can call.
"""

import csv
import io
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from django.db import connection
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.responses.census import FAMILY_FIELDS
from parishkit.stewardship.responses.comparison import ADDRESS_COMPONENTS
from parishkit.stewardship.responses.member_census import MEMBER_FIELDS
from parishkit.stewardship.responses.member_requests import REQUEST_FIELDS
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.source.snapshot_names import name_rows
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.dates import UnknownZone, browser_day_start
from parishkit.stewardship.web.exports import csv_cell

from .information_rendering import xlsx_cell

# Plain labels for "What changed", from the census forms' own field labels.
LABELS = {
    **{field.name: field.label for field in FAMILY_FIELDS},
    **{field.name: field.label for field in MEMBER_FIELDS},
    **{field.name: field.label for field in REQUEST_FIELDS},
    "new_member": "New Member",
}
MEMBER_LABELS = {field.name: field.label for field in MEMBER_FIELDS}

# Statuses in the order the filter lists them; the first three are the work
# still open. History rows (superseded or cancelled) appear only on request.
STATUSES = {
    "to_review": "To review",
    "to_do": "To do",
    "conflict": "Conflict",
    "being_published": "Being published",
    "published": "Published",
    "already": "Already in ParishSoft",
    "entered": "Entered by hand",
    "ignored": "Ignored",
    "superseded": "Superseded",
    "cancelled": "Cancelled",
}
HISTORY = frozenset({"superseded", "cancelled"})
# A terminal execution is the outcome, whatever the decision says.
TERMINAL = {
    "published": "published",
    "resolved_upstream": "already",
    "resolved_external": "entered",
    "superseded": "superseded",
    "cancelled": "cancelled",
}
STATUS_CHOICES = ("open", *[key for key in STATUSES if key not in HISTORY], "all")
ROUTES = ("any", "automatic", "by_hand")
KINDS = ("any", "contact", "moved", "deceased", "new_member")
EARLIEST, LATEST = date(2000, 1, 1), date(2999, 12, 31)
HEADINGS = (
    "Family",
    "Family DUID",
    "Who",
    "What changed",
    "ParishSoft now",
    "Family's answer",
    "Edited value",
    "How it reaches ParishSoft",
    "Status",
    "Submitted",
)


@dataclass(frozen=True, repr=False)
class CensusQuery:
    """The page's filters; identifying search text travels only in POST bodies.

    ``status`` "open" is the default view: To do and Conflict, plus To review
    for an Administrator, who decides automatic changes. ``start`` and
    ``end`` are whole days in the browser's ``zone``, as on System logs.
    """

    search: str = ""
    status: str = "open"
    history: str = ""
    route: str = "any"
    kind: str = "any"
    start: str = ""
    end: str = ""
    zone: str = ""

    @classmethod
    def parse(cls, parameters):
        """Accept only single bounded values from the closed choices."""
        if type(parameters) is dict:
            parameters = MultiValueDict(
                {key: [value] for key, value in parameters.items()}
            )
        query = cls(**filters(parameters, allowed=set(cls.__dataclass_fields__)))
        bounded_text(query.search)
        if (
            query.status not in STATUS_CHOICES
            or query.history not in {"", "yes"}
            or query.route not in ROUTES
            or query.kind not in KINDS
        ):
            raise ValueError("Invalid census change filter.")
        for value in (query.start, query.end):
            if not value:
                continue
            day = date.fromisoformat(value)
            if day.isoformat() != value or not EARLIEST <= day <= LATEST:
                raise ValueError("Invalid census change date filter.")
        if query.start and query.end and query.start > query.end:
            raise ValueError("Invalid census change date interval.")
        if query.zone not in timezone_names():
            # Days cannot be placed without the browser's zone; without days
            # the zone is unused, so an unknown one is simply dropped.
            if query.start or query.end:
                raise UnknownZone("Census change dates need the browser's zone.")
            query = replace(query, zone="")
        return query

    def form_values(self):
        """Values for CSRF-protected re-submission and downloads."""
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @property
    def bounds(self):
        """The From and Through days as a UTC interval; None where unset."""
        start, end = (
            date.fromisoformat(value) if value else None
            for value in (self.start, self.end)
        )
        return (
            browser_day_start(start, self.zone) if start else None,
            browser_day_start(end + timedelta(days=1), self.zone) if end else None,
        )


# One row per proposal of this campaign's live responses. Family names come
# from the current ParishSoft source, as in the talents report (the surname
# here; ``census_changes`` adds the heads from the returned snapshot); a
# Member's name is the Family's own corrected one first, then the parish
# record's.
ROWS = """
SELECT p.id::text, p.entity_kind, p.entity_key, p.field,
    p.baseline_available, p.baseline_value::text, p.submitted_value::text,
    p.current_available, p.current_value::text, p.admin_value_set,
    p.admin_value::text,
    p.handling, p.decision, p.execution, s.submitted_at, f.family_duid,
    coalesce(nullif(btrim(fp.canonical::jsonb->>'lastName'),''),
        nullif(btrim(fp.canonical::jsonb->>'mailingName'),''),
        'Unavailable Family') AS family_name,
    coalesce(nullif(btrim(concat_ws(' ',
            nullif(btrim(s.answers->'members'->p.entity_key->>'first_name'),''),
            nullif(btrim(s.answers->'members'->p.entity_key->>'last_name'),''))),''),
        nullif(btrim(concat_ws(' ',
            nullif(btrim(sm.canonical::jsonb->>'firstName'),''),
            nullif(btrim(sm.canonical::jsonb->>'lastName'),''))),'')) AS member_name,
    sc.snapshot_id
FROM stewardship_campaign c
LEFT JOIN stewardship_source_current sc ON sc.singleton
JOIN stewardship_submission s ON s.campaign_id=c.id AND s.mode='live'
JOIN stewardship_proposed_change p ON p.submission_id=s.id
JOIN stewardship_family_campaign f ON f.id=s.family_id
LEFT JOIN stewardship_snapshot_family fm
    ON fm.snapshot_id=sc.snapshot_id AND fm.source_key=f.family_duid::text
LEFT JOIN stewardship_source_family fp ON fp.id=fm.payload_id
LEFT JOIN stewardship_snapshot_member mm ON p.entity_kind='member'
    AND mm.snapshot_id=sc.snapshot_id AND mm.source_key=p.entity_key
LEFT JOIN stewardship_source_member sm ON sm.id=mm.payload_id
WHERE c.id=%s AND p.handling<>'report-only'
ORDER BY f.family_duid, s.submitted_at, p.entity_kind,
    p.entity_key, p.field
"""
COLUMNS = (
    "id",
    "entity_kind",
    "entity_key",
    "field",
    "baseline_available",
    "baseline_value",
    "submitted_value",
    "current_available",
    "current_value",
    "admin_value_set",
    "admin_value",
    "handling",
    "decision",
    "execution",
    "submitted_at",
    "family_duid",
    "family_name",
    "member_name",
    "snapshot_id",
)

JSON_COLUMNS = ("baseline_value", "submitted_value", "current_value", "admin_value")


def census_changes(campaign_id, query, principal):
    """Read, word and filter the campaign's census changes for Admin or Staff."""
    if not allows(principal, Capability.VIEW_CENSUS):
        raise PermissionError("Census changes are unavailable.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM stewardship_campaign WHERE id=%s", [campaign_id])
        if cursor.fetchone() is None:
            raise ReadUnavailable("Census change inputs are unavailable.")
        cursor.execute(ROWS, [campaign_id])
        rows = [dict(zip(COLUMNS, values, strict=True)) for values in cursor]
    for row in rows:
        # JSON values arrive as text (cast in ROWS), whatever the driver's
        # own JSON handling, and are decoded once here.
        for name in JSON_COLUMNS:
            row[name] = None if row[name] is None else json.loads(row[name])
    if rows:
        # Name each Family "Squyres, Jeff and Tracy", as every Admin table
        # does (#932), from the same snapshot this read used; the search,
        # sort and downloads all use that name.
        name_rows(rows[0]["snapshot_id"], rows)
    administrator = allows(principal, Capability.PUBLISH_CENSUS)
    return select([shape(row) for row in rows], query, administrator=administrator)


def automatic(row):
    """Whether publication may carry this change to ParishSoft.

    Only fields the handling registry marks API-writable, and never a Family
    address: no verified ParishSoft address read exists, so publication could
    neither detect a conflict nor confirm the write (reports spec, "Manual
    census resolution"). Those are worked by hand until one does.
    """
    return row["handling"] == "api" and row["entity_kind"] != "family"


def status(row):
    """The row's status key, from its decision and execution.

    A terminal execution decides first, since it is the outcome; then an
    ignored decision; then the execution. A pending automatic change no
    Administrator has decided yet is To review; other pending rows, and a
    failed publication, are To do.
    """
    execution, decision = row["execution"], row["decision"]
    if execution in TERMINAL:
        return TERMINAL[execution]
    if decision == "ignored":
        return "ignored"
    if execution == "queued":
        return "being_published"
    if execution == "conflict":
        return "conflict"
    if execution == "pending" and decision == "unreviewed" and automatic(row):
        return "to_review"
    return "to_do"


def kind(row):
    """The kind-of-change filter's group for the row."""
    if row["field"] == "new_member":
        return "new_member"
    if row["field"] == "moved_household":
        return "moved"
    if row["field"] in {"deceased_status", "death_date"}:
        return "deceased"
    return "contact"


def display(value):
    """A stored value as plain text: an address on one line, a Member's name
    and details, Yes or No for a choice, and an empty string for none."""
    if value is None:
        return ""
    if type(value) is bool:
        return "Yes" if value else "No"
    if isinstance(value, list):
        return "; ".join(display(item) for item in value)
    if not isinstance(value, dict):
        return str(value)
    if "display" in value:
        # A phone keeps its own display alongside its comparison key.
        return display(value["display"])
    if set(value) <= set(ADDRESS_COMPONENTS):
        return ", ".join(
            display(value[part]) for part in ADDRESS_COMPONENTS if value.get(part)
        )
    if set(value) <= set(MEMBER_LABELS):
        name = " ".join(
            display(value[part])
            for part in ("first_name", "last_name")
            if value.get(part)
        )
        details = [
            f"{MEMBER_LABELS[key]}: {display(item)}"
            for key, item in value.items()
            if key not in {"first_name", "last_name"} and display(item)
        ]
        return "; ".join(part for part in (name, *details) if part)
    return "; ".join(f"{key}: {display(item)}" for key, item in value.items())


def shape(row):
    """Word one proposal row for the page and the downloads."""
    shown = row | {
        "status": status(row),
        "automatic": automatic(row),
        "kind": kind(row),
        "label": LABELS.get(row["field"], row["field"]),
        "family_answer": display(row["submitted_value"]),
        "edited": display(row["admin_value"]) if row["admin_value_set"] else "",
        # Blank where no verified ParishSoft read exists (every Family
        # household field today) rather than a value that is not there.
        "parishsoft_now": (
            display(row["current_value"])
            if row["current_available"] and row["entity_kind"] != "family"
            else ""
        ),
        "baseline": (
            display(row["baseline_value"]) if row["baseline_available"] else ""
        ),
    }
    if row["entity_kind"] == "family":
        shown["who"] = "Family"
    elif row["entity_kind"] == "proposed_member":
        name = display(
            {
                key: (row["submitted_value"] or {}).get(key)
                for key in ("first_name", "last_name")
            }
        )
        shown["who"] = f"New Member: {name}" if name else "New Member"
    else:
        shown["who"] = row["member_name"] or "Unavailable name"
    shown["status_label"] = STATUSES[shown["status"]]
    shown["route_label"] = "Automatic" if shown["automatic"] else "By hand"
    if isinstance(shown["submitted_at"], str):
        shown["submitted_at"] = datetime.fromisoformat(shown["submitted_at"])
    return shown


def wanted(status_key, query, *, administrator):
    """Whether the status filter keeps a row with ``status_key``."""
    if status_key in HISTORY:
        return bool(query.history)
    if query.status == "all":
        return True
    if query.status == "open":
        return status_key in {"to_do", "conflict"} or (
            administrator and status_key == "to_review"
        )
    return status_key == query.status


def select(rows, query, *, administrator):
    """The rows the filters keep, with counts by status for the summary."""
    lower, upper = query.bounds
    needle = query.search.casefold()
    kept = [
        row
        for row in rows
        if wanted(row["status"], query, administrator=administrator)
        and (query.route == "any" or (query.route == "automatic") == row["automatic"])
        and (query.kind == "any" or query.kind == row["kind"])
        and (lower is None or row["submitted_at"] >= lower)
        and (upper is None or row["submitted_at"] < upper)
        and (
            not needle
            or needle in row["family_name"].casefold()
            or needle in row["who"].casefold()
            or needle in str(row["family_duid"])
        )
    ]
    # The summary counts every status, and history only when it is shown.
    counts = {key: 0 for key in STATUSES}
    for row in rows:
        if row["status"] not in HISTORY or query.history:
            counts[row["status"]] += 1
    return {
        "rows": kept,
        "summary": [
            (label, counts[key]) for key, label in STATUSES.items() if counts[key]
        ],
        "administrator": administrator,
    }


def export_rows(result, zone):
    """The filtered rows as plain text, times in the requested timezone."""
    return [
        (
            row["family_name"],
            str(row["family_duid"]),
            row["who"],
            row["label"],
            row["parishsoft_now"],
            row["family_answer"],
            row["edited"],
            row["route_label"],
            row["status_label"],
            row["submitted_at"].astimezone(zone).isoformat(timespec="seconds"),
        )
        for row in result["rows"]
    ]


def census_csv(result, zone):
    """One CSV of the filtered rows, with the page's columns."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADINGS)
    writer.writerows(
        [csv_cell(value) for value in row] for row in export_rows(result, zone)
    )
    return buffer.getvalue().encode("utf-8")


def census_xlsx(result, zone):
    """A workbook with one literal-text sheet of the filtered rows."""
    # Imported here, like the other export writers, to keep web startup light.
    from openpyxl import Workbook

    from .xlsx_design import style_table

    book = Workbook()
    sheet = book.active
    sheet.title = "Census changes"
    for row_number, values in enumerate((HEADINGS, *export_rows(result, zone)), 1):
        for column, value in enumerate(values, start=1):
            # Literal text: a Family-typed "=..." is never a formula.
            xlsx_cell(sheet, row_number, column, value)
    style_table(sheet, freeze_first_column=True, title="Census changes")
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()
