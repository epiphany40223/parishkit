"""One immutable multi-Ministry follow-up packet for CSV, XLSX and PDF.

A packet is a working sheet: recorded workflow values are prefilled and every
other cell stays blank so it can be completed by hand. Rendering uses only the
captured document, never current source or workflow data.
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime
from itertools import chain
from zoneinfo import ZoneInfo

from parishkit.stewardship.web.exports import csv_cell

from .information_rendering import (
    FORMAT_NOTE,
    PAGE_LINES,
    record_lines,
    visible_text,
    write_pages,
)
from .ministries import OUTCOMES, STATES

TITLE = "Ministry follow-up packet"
# The spreadsheet format's per-cell limit. The library truncates beyond it
# silently, which would publish an incomplete packet that looks complete.
MAX_CELL = 32767
# Worksheet titles the spreadsheet application reserves, compared caselessly.
RESERVED_TITLES = frozenset({"history", "report information"})
HEADINGS = (
    "Member",
    "Member DUID",
    "Request",
    "Status",
    "Email address(es)",
    "Email contact date",
    "Phone number(s)",
    "Phone contact date",
    "Outcome",
)


@dataclass(frozen=True, repr=False)
class PacketSection:
    """One Ministry: its printed header lines and complete ordered rows."""

    name: str
    details: tuple[tuple[str, str], ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, repr=False)
class PacketDocument:
    """Detached strings only; rendering never queries current private data."""

    metadata: tuple[tuple[str, str], ...]
    sections: tuple[PacketSection, ...]
    item_count: int
    requested_at: datetime
    title: str = TITLE
    headings: tuple[str, ...] = HEADINGS


def packet_document(payload, parameters, *, parish_name, requested_at, timezone):
    """Build the packet from one capture, preserving its Ministry and row order."""
    zone = ZoneInfo(timezone)

    def moment(value):
        """Parse an aware captured instant into the requester's display zone."""
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("Ministry packet timestamps must be aware.")
        return parsed.astimezone(zone)

    def contacted(value):
        """A recorded contact date, or blank for completion by hand."""
        return moment(value).date().isoformat() if value else ""

    def contacts(item, kind):
        """Leaders see only published contact values, as in the Ministry report."""
        if item[f"{kind}_visibility"] == "not_published":
            return "Not published"
        if kind == "email":
            values = [row.get("value") for row in item["emails"] or []]
        else:
            values = [
                f"{label}: {value}" if value else None
                for label, value in (item["phones"] or {}).items()
            ]
        return "\n".join(value for value in values if value)

    def outcome(item):
        """The specification's exact labels; an unresolved request is blank.

        Only Other carries its notes/reference, which SQL captured for it alone.
        """
        label = OUTCOMES.get(item["outcome"], "")
        if item["outcome"] == "other" and item.get("notes"):
            return f"{label}: {item['notes']}"
        return label

    source = payload["metadata"]
    period = f"{source['start_date']} to {source['end_date']}"
    sections = tuple(
        PacketSection(
            name=entry["name"],
            details=(
                ("Ministry", entry["name"]),
                ("Ministry DUID", str(entry["duid"])),
                ("Chairs", ", ".join(entry["chairs"]) or "None recorded"),
                ("Stewardship campaign", source["name"]),
                ("Stewardship period", period),
            ),
            rows=tuple(
                (
                    item["member_name"],
                    str(item["member_duid"]) if item["member_duid"] else "",
                    "Join" if item["action"] == "join" else "Leave",
                    STATES[item["state"]],
                    contacts(item, "email"),
                    contacted(item["email_contact_at"]),
                    contacts(item, "phone"),
                    contacted(item["phone_contact_at"]),
                    outcome(item),
                )
                for item in entry["rows"]
            ),
        )
        for entry in payload["sections"]
    )
    count = sum(len(section.rows) for section in sections)
    if payload["total"] != count:
        raise ValueError("Ministry packets require the complete result.")
    history = parameters["filters"]["history"] == "all"
    selection = parameters["ministries"]
    metadata = (
        ("Report", TITLE),
        ("Parish", parish_name),
        ("Campaign", source["name"]),
        ("Campaign reference", source["id"]),
        ("Stewardship period", period),
        ("Source reference", source["source_id"]),
        ("Source generation", f"{source['source_generation']:,}"),
        ("Source as of", moment(source["source_as_of"]).isoformat(timespec="seconds")),
        ("Captured at", moment(source["observed_at"]).isoformat(timespec="seconds")),
        ("Requested at", moment(requested_at).isoformat(timespec="seconds")),
        ("Display timezone", timezone),
        (
            "Ministries",
            "All authorized Ministries"
            if selection is None
            else f"{len(selection):,} selected",
        ),
        (
            "Requests",
            "Latest requests, including resolved and withdrawn"
            if history
            else "Unresolved latest requests",
        ),
        ("Ministry sections", f"{len(sections):,}"),
        ("Request rows", f"{count:,}"),
        (
            "Blank cells",
            "Nothing is recorded yet; complete them by hand. A blank outcome is an "
            "unresolved request.",
        ),
    )
    return PacketDocument(metadata, sections, count, requested_at)


def packet_csv(document, output):
    """One file: metadata, then each Ministry with its own repeated headings."""
    wrapper = io.TextIOWrapper(output, encoding="utf-8", newline="", write_through=True)
    try:
        writer = csv.writer(wrapper, lineterminator="\r\n")
        for key, value in document.metadata:
            writer.writerow((key, csv_cell(value)))
        for section in document.sections:
            writer.writerow(())
            for key, value in section.details:
                writer.writerow((key, csv_cell(value)))
            writer.writerow(document.headings)
            for row in section.rows:
                writer.writerow(map(csv_cell, row))
        wrapper.flush()
    finally:
        wrapper.detach()


def sheet_names(names):
    """Valid, distinct worksheet titles within the format's 31-character limit.

    Ministry names may repeat, collide once truncated, or contain characters a
    worksheet title forbids. Suffix a counter, shortening further to fit it.
    """

    def fit(text, length):
        """Truncate, then trim again: the cut itself can expose an apostrophe."""
        return text[:length].strip(" '")

    used, result = set(RESERVED_TITLES), []
    for name in names:
        base = fit(re.sub(r"[\[\]:*?/\\]", " ", visible_text(name)), 31) or "Ministry"
        title, counter = base, 1
        while title.lower() in used:
            counter += 1
            suffix = f" ({counter})"
            title = (fit(base, 31 - len(suffix)) or "Ministry") + suffix
        used.add(title.lower())
        result.append(title)
    return result


def packet_xlsx(document, output):
    """One workbook with a literal-text sheet per Ministry, plus report information."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    def write(sheet, row, column, value, *, bold=False):
        """Literal strings only, so no captured value can become a formula."""
        text = visible_text(value)
        if len(text) > MAX_CELL:
            raise ValueError("A packet value exceeds the spreadsheet cell limit.")
        cell = sheet.cell(row, column, text)
        cell.data_type = "s"
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        cell.font = Font(bold=bold)

    book = Workbook()
    try:
        book.remove(book.active)
        titles = sheet_names(section.name for section in document.sections)
        for title, section in zip(titles, document.sections, strict=True):
            sheet = book.create_sheet(title)
            for index, (key, value) in enumerate(section.details, 1):
                write(sheet, index, 1, key, bold=True)
                write(sheet, index, 2, value)
            head = len(section.details) + 2
            for column, heading in enumerate(document.headings, 1):
                write(sheet, head, column, heading, bold=True)
                sheet.column_dimensions[get_column_letter(column)].width = (
                    48 if heading == "Outcome" else 28
                )
            for offset, row in enumerate(section.rows, 1):
                for column, value in enumerate(row, 1):
                    write(sheet, head + offset, column, value)
            sheet.freeze_panes = sheet.cell(head + 1, 1)
            sheet.print_title_rows = f"{head}:{head}"
            sheet.page_setup.orientation = "landscape"
            sheet.page_setup.fitToWidth = 1
            sheet.sheet_properties.pageSetUpPr.fitToPage = True
            sheet.oddFooter.center.text = "Page &P of &N"
        information = book.create_sheet("Report information")
        for index, (key, value) in enumerate(
            chain(document.metadata, (("Text representation", FORMAT_NOTE),)), 1
        ):
            write(information, index, 1, key, bold=True)
            write(information, index, 2, value)
        information.column_dimensions["A"].width = 32
        information.column_dimensions["B"].width = 90
        book.save(output)
    finally:
        book.close()


def packet_pages(document):
    """Paginate so the report information and each Ministry start a new page."""
    groups = chain(
        ((document.metadata, (("Text representation", FORMAT_NOTE),)),),
        (
            chain(
                (section.details,),
                (zip(document.headings, row, strict=True) for row in section.rows),
            )
            for section in document.sections
        ),
    )
    for group in groups:
        lines = tuple(record_lines(group))
        for start in range(0, len(lines), PAGE_LINES):
            yield lines[start : start + PAGE_LINES]


def packet_pdf(document, output):
    """Count pages first so every page can state its position in the packet."""
    page_count = sum(1 for _ in packet_pages(document))
    return write_pages(document, output, packet_pages(document), page_count)


def render_packet(document, output, *, format):
    """Only three explicitly compiled packet formats are executable."""
    renderers = {"csv": packet_csv, "xlsx": packet_xlsx, "pdf": packet_pdf}
    try:
        renderer = renderers[format]
    except (KeyError, TypeError) as error:
        raise ValueError("Unsupported Ministry packet format.") from error
    return renderer(document, output)
