"""Limitations report over effective live responses (#247, #970).

Lists Members who cannot participate in ministries, and Families who cannot
attend Mass or prayer services. The report was "Talents and limitations"
until talents were retired (#970). Its SQL
(``stewardship_talent_report_v1``) is unchanged, so it still takes a
``talent`` parameter and returns stored talents; this module passes only
the limitation words and leaves the stored talents out of every row.
"""

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime

from django.db import connection
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.source.snapshot_names import name_rows
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.exports import csv_cell

from .information_rendering import xlsx_cell

# The Show menu's choices: everything, or one of the two limitations.
SHOW_WORDS = frozenset({"any", "cannot_serve", "cannot_attend"})
MEMBER_HEADINGS = (
    "Family",
    "Family DUID",
    "Member",
    "Cannot participate in ministries",
    "Latest response",
)
FAMILY_HEADINGS = ("Family", "Family DUID", "Cannot attend Mass", "Latest response")


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
        """Values for CSRF-protected re-submission and downloads."""
        return {"search": self.search, "show": self.show}

    def sql_parameters(self):
        """The report function's closed parameters (its filter is ``talent``)."""
        return {"search": self.search, "talent": self.show}


def limitations_report(campaign_id, query, principal):
    """Read and word the complete report for Admin or Staff."""
    if not allows(principal, Capability.CAMPAIGN_REPORT):
        raise PermissionError("The Limitations report is unavailable.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_talent_report_v1(%s,%s::jsonb)::text",
            [campaign_id, json.dumps(query.sql_parameters())],
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
    """Keep only the limitations, with parsed times, for page and files.

    The SQL also lists Members who chose talents before #970 and counts
    those talents; both are dropped, so only Members who cannot participate
    remain, and no row or summary carries stored talents.
    """
    result["members"] = [row for row in result["members"] if row["cannot_serve"]]
    for row in result["members"] + result["families"]:
        row.pop("talents", None)
        row["submitted_at"] = datetime.fromisoformat(row["submitted_at"])
    summary = result["summary"]
    result["summary"] = {
        "cannot_serve": summary["cannot_serve"],
        "cannot_attend": summary["cannot_attend"],
    }
    return result


def export_tables(result, zone):
    """Two plain tables (Members, then Families) with localized timestamps."""

    def instant(value):
        """Show each response time in the requested display timezone."""
        return value.astimezone(zone).isoformat(timespec="seconds")

    members = [
        (
            row["family_name"],
            str(row["family_duid"]),
            row["member_name"] + (" (proposed)" if row["proposed"] else ""),
            "Yes",
            instant(row["submitted_at"]),
        )
        for row in result["members"]
    ]
    families = [
        (
            row["family_name"],
            str(row["family_duid"]),
            "Yes",
            instant(row["submitted_at"]),
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


def limitations_xlsx(result, zone):
    """A workbook with one literal-text sheet each for Members and Families."""
    # Imported here, like the other export writers, to keep web startup light.
    from openpyxl import Workbook

    from .xlsx_design import style_table

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
        style_table(sheet, freeze_first_column=True, title=f"Limitations: {title}")
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()
