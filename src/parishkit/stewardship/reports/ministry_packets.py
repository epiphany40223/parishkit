"""One immutable multi-Ministry follow-up packet for CSV, XLSX and PDF.

A packet is a working sheet: recorded workflow values are prefilled and every
other cell stays blank so it can be completed by hand. Rendering uses only the
captured document, never current source or workflow data.
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from itertools import chain
from zoneinfo import ZoneInfo

from parishkit.stewardship.web import dates
from parishkit.stewardship.web.exports import csv_cell
from parishkit.stewardship.web.presentation import campaign_year
from parishkit.stewardship.web.presentation import phone as format_phone

from .information_rendering import FORMAT_NOTE, visible_text, xlsx_cell
from .ministries import NOT_IN_CAMPAIGN, OUTCOMES, STATE_LABELS

TITLE = "Ministry follow-up packet"
# The spreadsheet format's per-cell character limit. The library truncates
# beyond it silently, which would publish an incomplete packet that looks whole.
MAX_CELL_CHARACTERS = 32767
# Titles no Ministry sheet may take, compared caselessly: the spreadsheet
# application reserves History, and this workbook adds its own provenance sheet.
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
        return moment(value).date() if value else ""

    def contacts(item, kind):
        """Leaders see only published contact values, as in the Ministry report."""
        if item[f"{kind}_visibility"] == "not_published":
            return "Not published"
        if kind == "email":
            values = [row.get("value") for row in item["emails"] or []]
        else:
            values = [
                f"{label}: {format_phone(value)}" if value else None
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
    period = dates.Span(
        date.fromisoformat(source["start_date"]), date.fromisoformat(source["end_date"])
    )
    # The same single meaning of a campaign's year as Admin previews, page
    # blocks and share labels: the configured label, else the financial
    # period's start year, else the campaign start year. The captured metadata
    # carries exactly the keys that rule reads.
    year = campaign_year(source)
    sections = tuple(
        PacketSection(
            name=entry["name"],
            details=(
                ("Ministry", entry["name"]),
                ("Ministry DUID", str(entry["duid"])),
                # Removed from a live campaign, with requests kept (#342).
                # Captures made before the flag existed carry no such key.
                *(
                    (("Campaign selection", NOT_IN_CAMPAIGN),)
                    if entry.get("in_campaign") is False
                    else ()
                ),
                ("Chairs", ", ".join(entry["chairs"]) or "None recorded"),
                ("Stewardship campaign", source["name"]),
                ("Stewardship year", year),
                ("Stewardship period", period),
            ),
            rows=tuple(
                (
                    item["member_name"],
                    str(item["member_duid"]) if item["member_duid"] else "",
                    "Join" if item["action"] == "join" else "Leave",
                    STATE_LABELS[item["state"]],
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
        ("Stewardship year", year),
        ("Stewardship period", period),
        ("Source reference", source["source_id"]),
        ("Source generation", f"{source['source_generation']:,}"),
        ("Source as of", moment(source["source_as_of"])),
        ("Captured at", moment(source["observed_at"])),
        ("Requested at", moment(requested_at)),
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
        # The PDF footer, the XLSX information sheet and the CSV head all
        # carry this line, as every other report's files do.
        (
            "Privacy",
            "Sensitive parish information. Share only with authorized recipients.",
        ),
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
        """Trim, truncate, then trim again: the cut can expose an apostrophe.

        Trimming first keeps leading spaces, such as those replacing forbidden
        characters, from using up the title's 31 characters.
        """
        return text.strip(" '")[:length].strip(" '")

    used, result = set(RESERVED_TITLES), []
    for name in names:
        base = fit(re.sub(r"[\[\]:*?/\\]", " ", visible_text(name)), 31) or "Ministry"
        title, counter = base, 1
        while title.lower() in used:
            counter += 1
            suffix = f" ({counter})"
            title = fit(base, 31 - len(suffix)) + suffix
        used.add(title.lower())
        result.append(title)
    return result


def packet_xlsx(document, output):
    """One workbook with a literal-text sheet per Ministry, plus report information."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment

    from .xlsx_design import style_details, style_information, style_table

    def write(sheet, row, column, value):
        """Literal strings or native dates, so no value can become a formula."""
        if (
            not isinstance(value, date)
            and len(visible_text(dates.display_text(value))) > MAX_CELL_CHARACTERS
        ):
            raise ValueError("A packet value exceeds the spreadsheet cell limit.")
        cell = xlsx_cell(sheet, row, column, value)
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    book = Workbook()
    try:
        book.remove(book.active)
        titles = sheet_names(section.name for section in document.sections)
        for title, section in zip(titles, document.sections, strict=True):
            sheet = book.create_sheet(title)
            for index, (key, value) in enumerate(section.details, 1):
                write(sheet, index, 1, key)
                write(sheet, index, 2, value)
            head = len(section.details) + 2
            for column, heading in enumerate(document.headings, 1):
                write(sheet, head, column, heading)
            for offset, row in enumerate(section.rows, 1):
                for column, value in enumerate(row, 1):
                    write(sheet, head + offset, column, value)
            # The Ministry's details sit above its member table, labeled
            # like the report information; the table's header stays frozen.
            # Each detail value spans the table's width rather than wrapping
            # inside the narrow column the table gave column B.
            style_table(
                sheet, header_row=head, widths={"Outcome": 48}, title=document.title
            )
            style_details(
                sheet, range(1, head - 1), widths=False, span=len(document.headings)
            )
        information = book.create_sheet("Report information")
        for index, (key, value) in enumerate(
            chain(document.metadata, (("Text representation", FORMAT_NOTE),)), 1
        ):
            write(information, index, 1, key)
            write(information, index, 2, value)
        style_information(information, title=document.title)
        book.save(output)
    finally:
        book.close()


def packet_records(document):
    """The report-information card, then each Ministry's details and members.

    Each Ministry's details card starts a new page. Blank cells print as
    write-in rules, since a packet is completed by hand.
    """
    from .pdf_design import detail_record, report_record, row_records

    values = chain(
        (value for section in document.sections for _, value in section.details),
        (
            value
            for section in document.sections
            for row in section.rows
            for value in row
        ),
    )
    yield report_record(document.metadata, values=values)
    for section in document.sections:
        yield detail_record(section.details, style="info", new_page=True)
        yield from row_records(document.headings, section.rows, keep_blank=True)


def packet_pdf(document, output):
    """Count pages first so every page can state its position in the packet."""
    from .pdf_design import PdfFrame, write_records

    return write_records(
        output,
        PdfFrame.for_document(document),
        lambda: packet_records(document),
        requested_at=document.requested_at,
    )


def render_packet(document, output, *, format):
    """Only three explicitly compiled packet formats are executable."""
    renderers = {"csv": packet_csv, "xlsx": packet_xlsx, "pdf": packet_pdf}
    try:
        renderer = renderers[format]
    except (KeyError, TypeError) as error:
        raise ValueError("Unsupported Ministry packet format.") from error
    return renderer(document, output)
