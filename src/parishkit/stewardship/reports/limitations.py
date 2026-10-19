"""Limitations report over effective live responses (#247, #970).

Lists Members who cannot participate in ministries, and Families who cannot
attend Mass or prayer services. The report was "Talents and limitations"
until talents were retired (#970); its SQL is
``stewardship_limitations_report_v2`` (migration 0134; v1 with each answer's
key and latest review decision, #1685), which neither lists nor counts the
talents older submissions recorded.
"""

import csv
import io
import json
from dataclasses import dataclass, replace
from datetime import datetime

from django.db import connection
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.pipeline import Stage, cannot_participate_placement
from parishkit.stewardship.source.snapshot_names import name_rows
from parishkit.stewardship.source.source_status import (
    STATUS_HEADING,
    family_status,
    source_status_label,
)
from parishkit.stewardship.web import dates
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.exports import csv_cell

from .information_rendering import xlsx_cell

# The Show menu's choices: everything, or one of the two limitations.
SHOW_WORDS = frozenset({"any", "cannot_serve", "cannot_attend"})
# The stream's stages, in the order the worklist's Stage filter lists them
# (review pipeline spec, "Cannot participate stream", #1685). An answer the
# Family no longer gives is gone from the report, so there is no History.
STAGES = (Stage.TO_REVIEW, Stage.TO_HANDLE, Stage.IGNORED, Stage.HANDLED)
# Each answer in words, by its kind: the worklist's Family's answer.
ANSWERS = {
    "member": "Cannot participate in any ministries",
    "proposed_member": "Cannot participate in any ministries",
    "family": "Cannot attend Mass or prayer services",
}
# Each download table has its page table's columns, names and order (#932,
# #966): the Members table leads with the Member and Member DUID, as on the
# page, and the Families table names each Family's limitation in its
# Limitation column, in the worklist's words (R39 finding 11), as the Members
# table's Cannot participate column does. ParishSoft status follows the
# Family's name, as the page's badge
# sits beside it: "Not in ParishSoft" with the name blank when the data no
# longer has the Family, otherwise blank (#1216).
MEMBER_HEADINGS = (
    "Member",
    "Member DUID",
    "Family",
    STATUS_HEADING,
    "Family DUID",
    "Cannot participate in ministries",
    "Latest response",
    "Stage",
)
FAMILY_HEADINGS = (
    "Family",
    STATUS_HEADING,
    "Family DUID",
    "Limitation",
    "Latest response",
    "Stage",
)
# Each table's page heading, which the PDF prints above that table.
MEMBER_CAPTION = "Members who cannot participate in ministries"
FAMILY_CAPTION = "Families that cannot attend Mass or prayer services"
# Relative PDF column widths, by heading: names and the response time get the
# room; the Yes columns and DUIDs are short.
PDF_WEIGHTS = {
    "Family": 10,
    STATUS_HEADING: 5,
    "Family DUID": 5,
    "Member": 9,
    "Member DUID": 5,
    "Cannot participate in ministries": 6,
    "Limitation": 9,
    "Latest response": 8,
    "Stage": 6,
}
PRIVACY = "Sensitive parish report. Share only with authorized recipients."
# The report's name in its files: the PDF title, XLSX headers and properties.
TITLE = "Limitations"


@dataclass(frozen=True, repr=False)
class LimitationsQuery:
    """Identifying search text travels only in CSRF POST bodies."""

    search: str = ""
    show: str = "any"

    @classmethod
    def parse(cls, parameters):
        """Accept only single bounded values; SQL enforces the same closed shape."""
        if type(parameters) is dict:
            parameters = MultiValueDict(
                {key: [value] for key, value in parameters.items()}
            )
        query = cls(**filters(parameters, allowed=set(cls.__dataclass_fields__)))
        bounded_text(query.search)
        if query.show not in SHOW_WORDS:
            raise ValueError("Invalid Limitations filter.")
        return query

    def form_values(self):
        """Values for re-submission and downloads, and the SQL's parameters."""
        return {"search": self.search, "show": self.show}


def limitations_report(campaign_id, query, principal):
    """Read and word the complete report for Admin or Staff."""
    if not allows(principal, Capability.CAMPAIGN_REPORT):
        raise PermissionError("The Limitations report is unavailable.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_limitations_report_v2(%s,%s::jsonb)::text",
            [campaign_id, json.dumps(query.form_values())],
        )
        value = cursor.fetchone()
    if value is None or value[0] is None:
        raise ReadUnavailable("Limitations report inputs are unavailable.")
    result = json.loads(value[0])
    if result.get("unavailable"):
        raise ReadUnavailable("Limitations report inputs are unavailable.")
    # Each Family reads "Squyres, Jeff and Tracy", as in every Admin table
    # (#932), from the snapshot SQL read; the page and its downloads share it.
    source = result["metadata"]["source_id"]
    name_rows(source, result["members"] + result["families"])
    return shape_result(result)


def shape_result(result):
    """Parse each row's times and place it in its pipeline stage (#1685).

    The stage comes from the answer's latest review decision while it is in
    force (``pipeline.cannot_participate_placement``); the page, its
    downloads and ``pk-admin`` read the same placement.
    """
    for row in result["members"] + result["families"]:
        row["submitted_at"] = datetime.fromisoformat(row["submitted_at"])
        if row.get("decided_at"):
            row["decided_at"] = datetime.fromisoformat(row["decided_at"])
        row["placement"] = cannot_participate_placement(
            decision=row.get("decision"), in_force=bool(row.get("in_force"))
        )
    return result


def at_stage(result, stage):
    """The report with only the answers at ``stage`` (a stage value, or "all").

    The worklist's download applies its Stage filter through here, so the
    file lists what the table shows; the summary counts what is left.
    """
    if stage == "all":
        return result

    def kept(rows):
        """The rows at the stage."""
        return [row for row in rows if row["placement"].stage.value == stage]

    members, families = kept(result["members"]), kept(result["families"])
    return result | {
        "members": members,
        "families": families,
        "summary": {"cannot_serve": len(members), "cannot_attend": len(families)},
    }


def iso_time(moment):
    """A response time already in the display zone, as ISO 8601 seconds."""
    return moment.isoformat(timespec="seconds")


def _family(row):
    """A row's Family name and ParishSoft status cells (#1216).

    The report reads no activity, so the only mark is a Family the data no
    longer has, whose name is blank.
    """
    name = row["family_name"]
    return name or "", source_status_label(family_status(name))


def export_tables(result, zone, *, time_text=iso_time):
    """Two plain tables (Members, then Families) with localized timestamps.

    ``time_text`` words each Latest response time already placed in
    ``zone``: ISO 8601 for CSV and XLSX, the parish's compact date format
    for the PDF. Every format reads these same rows (#966).
    """

    def instant(value):
        """Show each response time in the requested display timezone."""
        return time_text(value.astimezone(zone))

    members = [
        (
            row["member_name"] + (" (proposed)" if row["proposed"] else ""),
            # A proposed Member has no DUID yet, so its cell is empty.
            "" if row["member_duid"] is None else str(row["member_duid"]),
            *_family(row),
            str(row["family_duid"]),
            "Yes",
            instant(row["submitted_at"]),
            row["placement"].stage.label,
        )
        for row in result["members"]
    ]
    families = [
        (
            *_family(row),
            str(row["family_duid"]),
            ANSWERS[row["kind"]],
            instant(row["submitted_at"]),
            row["placement"].stage.label,
        )
        for row in result["families"]
    ]
    return members, families


def limitations_csv(result, zone):
    """One CSV: the Member table, a blank line, then the Family table."""
    members, families = export_tables(result, zone)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(MEMBER_HEADINGS)
    writer.writerows([csv_cell(value) for value in row] for row in members)
    writer.writerow([])
    writer.writerow(FAMILY_HEADINGS)
    writer.writerows([csv_cell(value) for value in row] for row in families)
    return buffer.getvalue().encode("utf-8")


def report_information(members, families, zone, *, parish, campaign, as_of):
    """The XLSX "Report information" rows: what the PDF's page frame states.

    The parish and campaign, the download time ``as_of`` in ``zone`` (a
    native date-time cell), both counts and the privacy line, labeled as
    the response lists' downloads label them. The CSV holds only the two
    tables, as every report's CSV holds only its table (#966).
    """
    return (
        ("Report", TITLE),
        ("Parish", parish),
        ("Campaign", campaign),
        ("Downloaded at", as_of.astimezone(zone)),
        ("Display time zone", zone.key),
        ("Members in this file", f"{len(members):,}"),
        ("Families in this file", f"{len(families):,}"),
        ("Privacy", PRIVACY),
    )


def limitations_xlsx(result, zone, *, parish, campaign, as_of):
    """A literal-text sheet each for Members and Families, then report information.

    ``parish`` and ``campaign`` are names and ``as_of`` the download time,
    as for ``limitations_pdf``; the "Report information" sheet and the
    workbook's document properties state them.
    """
    # Imported here, like the other export writers, to keep web startup light.
    from openpyxl import Workbook

    from .xlsx_design import describe, style_information, style_table

    members, families = export_tables(result, zone)
    book = Workbook()
    sheets = (
        (book.active, "Members", MEMBER_HEADINGS, members),
        (book.create_sheet(), "Families", FAMILY_HEADINGS, families),
    )
    for sheet, title, headings, rows in sheets:
        sheet.title = title
        for row_number, values in enumerate((headings, *rows), start=1):
            for column, value in enumerate(values, start=1):
                # The shared writer keeps text literal, so a parish-typed "=..."
                # is never a formula, and escapes characters XLSX cannot hold,
                # such as a vertical tab pasted into a ParishSoft name.
                xlsx_cell(sheet, row_number, column, value)
        style_table(sheet, freeze_first_column=True, title=f"{TITLE}: {title}")
    information = book.create_sheet("Report information")
    rows = report_information(
        members, families, zone, parish=parish, campaign=campaign, as_of=as_of
    )
    for row_number, values in enumerate(rows, start=1):
        for column, value in enumerate(values, start=1):
            xlsx_cell(information, row_number, column, value)
    style_information(information, title=TITLE)
    describe(book, TITLE, created=as_of)
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def limitations_pdf(result, zone, *, parish, campaign, as_of):
    """The Members table, then the Families table, as shared framed PDF tables.

    Each table is its CSV/XLSX headings over ``export_tables``' rows, so the
    three files never disagree (#966); only Latest response reads in the
    parish's compact date format. The Families table starts on a new page,
    and every page names its table with the page's table heading. The page
    frame names the parish and campaign, the download time ``as_of`` in
    ``zone`` and both counts. The caller lends the parish's date format
    (``dates.using``).
    """
    from .pdf_design import (
        ACCENT_STRONG,
        CELL_PAD,
        LABEL_SIZE,
        MARGIN,
        SMALL_PITCH,
        PdfFrame,
        Table,
        draw_table,
        table_pages,
    )

    members, families = export_tables(
        result, zone, time_text=lambda moment: dates.display_text(moment, compact=True)
    )
    frame = PdfFrame(
        title=TITLE,
        eyebrow=f"{parish} · {campaign}",
        stamp=f"Downloaded {dates.display_text(as_of.astimezone(zone))} ({zone.key})",
        details=(
            f"{len(members):,} {'Member' if len(members) == 1 else 'Members'} "
            "cannot participate in ministries; "
            f"{len(families):,} {'Family' if len(families) == 1 else 'Families'} "
            "cannot attend Mass.",
        ),
        notice=PRIVACY,
    )
    # The tables lay out in a frame two blank detail lines taller, so the
    # caption fits between the header rule and the table on every page.
    body = replace(frame, details=(*frame.details, "", ""))

    def drawer(caption, table):
        """Draw one page of ``table`` under its ``caption``."""
        draw = draw_table(table, body)

        def draw_page(canvas, page):
            """The caption, then the table page."""
            canvas.text(
                MARGIN + CELL_PAD,
                frame.body_top + SMALL_PITCH,
                caption,
                LABEL_SIZE,
                "bold",
                ACCENT_STRONG,
            )
            draw(canvas, page)

        return draw_page

    # Each page carries the drawer of the table it belongs to, so one frame
    # holds both tables and numbers their pages together.
    pages = []
    for caption, headings, rows in (
        (MEMBER_CAPTION, MEMBER_HEADINGS, members),
        (FAMILY_CAPTION, FAMILY_HEADINGS, families),
    ):
        table = Table.weighted(headings, [PDF_WEIGHTS[name] for name in headings])
        draw = drawer(caption, table)
        pages += [(draw, page) for page in table_pages(table, rows, body)]
    output = io.BytesIO()
    frame.write(
        output,
        pages,
        len(pages),
        lambda canvas, page: page[0](canvas, page[1]),
        requested_at=as_of,
    )
    return output.getvalue()
