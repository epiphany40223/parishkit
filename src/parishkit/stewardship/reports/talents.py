"""Talents and limitations report over effective live responses (#247).

Lists Members who chose a talent or cannot participate in ministries, and
Families who cannot attend Mass or prayer services. Talent wording comes from
the campaign's current talent list (or the built-in defaults); a talent the
parish has since removed reads "Unavailable talent". A campaign that offers
no talents (its list was emptied, or it has no Ministry page) reports only
the limitations: no talent column, count or filter appears anywhere.
"""

import csv
import io
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime

from django.db import connection
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.responses.service import talent_options
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.exports import csv_cell

from .information_rendering import xlsx_cell

OPTION = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
UNAVAILABLE = "Unavailable talent"
MEMBER_HEADINGS = (
    "Family",
    "Family DUID",
    "Member",
    "Talents",
    "Cannot participate in ministries",
    "Latest response",
)
FAMILY_HEADINGS = ("Family", "Family DUID", "Cannot attend Mass", "Latest response")


@dataclass(frozen=True, repr=False)
class TalentQuery:
    """Identifying search text travels only in CSRF POST bodies."""

    search: str = ""
    talent: str = "any"

    @classmethod
    def parse(cls, parameters):
        """Accept only single bounded values; SQL enforces the same closed shape."""
        if type(parameters) is dict:
            parameters = MultiValueDict(
                {key: [value] for key, value in parameters.items()}
            )
        query = cls(**filters(parameters, allowed=set(cls.__dataclass_fields__)))
        bounded_text(query.search)
        if query.talent not in {
            "any",
            "cannot_serve",
            "cannot_attend",
        } and not OPTION.fullmatch(query.talent):
            raise ValueError("Invalid talent filter.")
        return query

    def form_values(self):
        """Values for CSRF-protected re-submission and downloads."""
        return {"search": self.search, "talent": self.talent}

    def offered(self, configuration):
        """This query, with a talent filter no longer offered read as "any".

        A page re-posted (sort, paging or download) after the parish removed
        that talent would otherwise filter every row out while the menu, which
        no longer lists it, shows "Everything".
        """
        if OPTION.fullmatch(self.talent) and self.talent not in talent_labels(
            configuration
        ):
            return replace(self, talent="any")
        return self


def talent_labels(configuration):
    """The campaign's talent wording by identity, in its configured order."""
    return {option.id: option.label for option in talent_options(configuration)}


def talents_report(campaign_id, query, principal, *, configuration):
    """Read and word the complete report for Admin or Staff."""
    if not allows(principal, Capability.CAMPAIGN_REPORT):
        raise PermissionError("The talents report is unavailable.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_talent_report_v1(%s,%s::jsonb)::text",
            [campaign_id, json.dumps(query.form_values())],
        )
        value = cursor.fetchone()
    if value is None or value[0] is None:
        raise ReadUnavailable("Talent report inputs are unavailable.")
    result = json.loads(value[0])
    if result.get("unavailable"):
        raise ReadUnavailable("Talent report inputs are unavailable.")
    return shape_result(result, configuration=configuration)


def shape_result(result, *, configuration):
    """Word talents in configured order, with any free text, for page and files.

    When the campaign offers no talents, older answers' talents are dropped
    along with the Members listed only for them, so the report is purely
    about limitations and says plainly that no talents are collected.
    """
    labels = talent_labels(configuration)
    result["collects_talents"] = bool(labels)
    if not labels:
        result["members"] = [row for row in result["members"] if row["cannot_serve"]]
        for row in result["members"]:
            row["talents"] = {}
        result["summary"]["members"] = len(result["members"])
        result["summary"]["talents"] = {}
    position = {key: index for index, key in enumerate(labels)}
    for row in result["members"]:
        row["talents"] = [
            labels.get(key, UNAVAILABLE) + (f": {text}" if text else "")
            for key, text in sorted(
                row["talents"].items(),
                key=lambda item: (position.get(item[0], len(position)), item[0]),
            )
        ]
        row["submitted_at"] = datetime.fromisoformat(row["submitted_at"])
    for row in result["families"]:
        row["submitted_at"] = datetime.fromisoformat(row["submitted_at"])
    counts = result["summary"]["talents"]
    # Talents the parish has since removed are counted together, so the
    # summary includes every talent that any listed Member chose.
    retired = sum(count for key, count in counts.items() if key not in labels)
    result["summary"]["talents"] = [
        (label, counts.get(key, 0)) for key, label in labels.items()
    ] + ([(UNAVAILABLE, retired)] if retired else [])
    result["talent_choices"] = list(labels.items())
    return result


def member_headings(result):
    """Member columns; the Talents column only when the campaign collects talents."""
    if result["collects_talents"]:
        return MEMBER_HEADINGS
    return tuple(heading for heading in MEMBER_HEADINGS if heading != "Talents")


def export_tables(result, zone):
    """Two plain tables (Members, then Families) with localized timestamps."""

    def instant(value):
        """Show each response time in the requested display timezone."""
        return value.astimezone(zone).isoformat(timespec="seconds")

    talents = result["collects_talents"]
    members = [
        (
            row["family_name"],
            str(row["family_duid"]),
            row["member_name"] + (" (proposed)" if row["proposed"] else ""),
            *(("; ".join(row["talents"]),) if talents else ()),
            "Yes" if row["cannot_serve"] else "",
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


def talents_csv(result, zone):
    """One CSV: the Member table, a blank line, then the Family table."""
    members, families = export_tables(result, zone)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for headings, rows in (
        (member_headings(result), members),
        (FAMILY_HEADINGS, families),
    ):
        if rows is families:
            writer.writerow([])
        writer.writerow(headings)
        writer.writerows([csv_cell(value) for value in row] for row in rows)
    return buffer.getvalue().encode("utf-8")


def talents_xlsx(result, zone):
    """A workbook with one literal-text sheet each for Members and Families."""
    # Imported here, like the other export writers, to keep web startup light.
    from openpyxl import Workbook

    members, families = export_tables(result, zone)
    book = Workbook()
    sheets = (
        (book.active, "Members", member_headings(result), members),
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
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()
