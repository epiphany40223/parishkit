"""One column set in every format of every Admin download (#966).

The Administrator's decision B1: a download's CSV, XLSX and PDF carry the
same columns, and none of them carries internal references (row versions,
reference UUIDs, generations). This guard renders every registered export
in each of its formats from one document and compares the columns:

- CSV: its header row (a Ministry packet repeats it per Ministry);
- XLSX: the table sheet's header row;
- PDF: the table's headings, or, for a card report, the fields its cards
  show plus the fields a card puts in its title (name, DUID, status).

It also checks that no heading or report-information label is in the
shared internal-field list, and that every export kind the export worker
accepts has a case here, so a new export cannot skip the guard. Talents is
left out (#970 removes it) and System logs is the recorded exception in the
UX spec's Downloaded files section.
"""

import csv
import io
import re
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook

from parishkit.stewardship.reports import family_test_names, pdf_design
from parishkit.stewardship.reports.census_changes import (
    CensusQuery,
    census_csv,
    census_pdf,
    census_xlsx,
    select,
)
from parishkit.stewardship.reports.directory_rendering import (
    address_blocks,
    render_directory,
)
from parishkit.stewardship.reports.export_models import ExportRequest
from parishkit.stewardship.reports.information_rendering import (
    INTERNAL_FIELDS,
    render_information,
)
from parishkit.stewardship.reports.ministry_packets import render_packet
from parishkit.stewardship.reports.participation import participation_csv
from parishkit.stewardship.reports.response_lists import LISTS, list_file
from parishkit.stewardship.reports.spreadsheets import participation_xlsx

from .test_census_changes import SUBMITTED
from .test_census_changes import rows as census_rows
from .test_directory_rendering import document as directory
from .test_directory_rendering import item as family
from .test_family_test_names import request as names_request
from .test_financial_documents import document as financial
from .test_financial_documents import row as financial_row
from .test_information_rendering import document as information
from .test_ministry_exports import document as ministry
from .test_ministry_packets import item as member
from .test_ministry_packets import packet
from .test_participation_rendering import document as participation
from .test_response_lists import NEW_YORK, details_of, rows_of

# The export worker's report kinds and the synchronous page downloads, each
# with the case below that renders it. "ministry" covers its summary, both
# lists and the multi-Ministry packet.
WORKER_CASES = {
    "participation": ("participation",),
    "additional_information": ("additional_information",),
    "family_directory": ("family_directory",),
    "postal_outreach": ("postal_outreach",),
    "ministry": ("ministry_summary", "ministry_join", "ministry_leave", "packet"),
    "financial": ("financial",),
    "family_test_names": ("family_test_names",),
}
PAGE_CASES = ("response_list", "census_changes")
# The postal mail merge's PDF is a sheet of address labels, not a table:
# each card holds the mailing columns, so it is checked by value instead.
ADDRESS_CARD_COLUMNS = (
    "ParishSoft DUID",
    "Family",
    "Addressee",
    "Address line 1",
    "Address line 2",
    "Address line 3",
    "City",
    "State",
    "ZIP",
    "Family code",
)


def worker_report_kinds():
    """Every ``report`` value the export request constraint accepts."""
    [constraint] = [
        item
        for item in ExportRequest._meta.constraints
        if item.name == "export_report_known"
    ]
    kinds = set()

    def walk(node):
        """Collect ``report=`` and ``report__in=`` values from the Q tree."""
        for child in node.children:
            if isinstance(child, tuple):
                key, value = child
                if key == "report":
                    kinds.add(value)
                elif key == "report__in":
                    kinds.update(value)
            else:
                walk(child)

    walk(constraint.condition)
    return kinds


def filled(document):
    """The document with every blank cell given a value.

    A PDF card leaves blank fields out, so a guard row has none; the CSV
    and XLSX are rendered from the same filled document.
    """
    return replace(
        document,
        rows=tuple(
            tuple("Filled" if value in (None, "") else value for value in row)
            for row in document.rows
        ),
    )


def render(renderer, document, format):
    """The bytes ``renderer(document, output, format=...)`` writes."""
    output = io.BytesIO()
    renderer(document, output, format=format)
    return output.getvalue()


def csv_headers(body, first=None):
    """Every header row of a CSV: its first row, plus any later row equal
    to it. A packet's report details come first and it repeats the headings
    for each Ministry, so its header rows are those starting with ``first``.
    """
    rows = list(csv.reader(io.StringIO(body.decode())))
    if first is not None:
        return [row for row in rows if row[:1] == [first]]
    return [row for row in rows if row == rows[0]] if rows else []


def xlsx_headers(body):
    """Each table sheet's header row: its first row with every cell filled.

    A packet's Ministry details sit above its table in two columns, so they
    are skipped; the "Report information" sheet is not a table.
    """
    book = load_workbook(io.BytesIO(body))
    try:
        headers = []
        for sheet in book.worksheets:
            if sheet.title == "Report information":
                continue
            headers.append(
                next(
                    list(row)
                    for row in sheet.iter_rows(values_only=True)
                    if all(value not in (None, "") for value in row)
                )
            )
        labels = (
            [row[0] for row in book["Report information"].iter_rows(values_only=True)]
            if "Report information" in book.sheetnames
            else []
        )
        return headers, labels
    finally:
        book.close()


def pdf_capture(monkeypatch, draw):
    """Run ``draw()`` and return what its PDF laid out: table headings, or
    the record cards (``write_records``)."""
    seen = {}
    pages, records = pdf_design.table_pages, pdf_design.write_records

    def table_pages(table, rows, frame):
        """Keep the table's headings, then paginate as usual."""
        seen["table"] = list(table.headings)
        return pages(table, rows, frame)

    def write_records(output, frame, factory, **options):
        """Keep the cards, then write them as usual."""
        seen["cards"] = list(factory())
        return records(output, frame, factory, **options)

    monkeypatch.setattr(pdf_design, "table_pages", table_pages)
    monkeypatch.setattr(pdf_design, "write_records", write_records)
    body = draw()
    assert body.startswith(b"%PDF")
    return seen


def card_columns(cards, headings):
    """The columns a card PDF shows, in heading order, and any other label.

    A card's title holds its name, DUID and status fields; a nested
    workflow revision's heading holds its Changed at and Changed by. The
    report-information and Ministry-details cards are not records.
    """
    shown = {field for field in pdf_design._identity(dict.fromkeys(headings)) if field}
    for card in cards:
        if card.style == "info":
            continue
        shown.update(label for label, _ in card.fields)
        for _, fields in card.children:
            shown.update(label for label, _ in fields)
            shown.update(pdf_design.CHANGE_FIELDS)
    return [heading for heading in headings if heading in shown], shown - set(headings)


def shared_case(monkeypatch, document, renderer=render_information):
    """A report rendered through one shared three-format renderer."""
    document = filled(document)
    headers = csv_headers(render(renderer, document, "csv"))
    sheets, labels = xlsx_headers(render(renderer, document, "xlsx"))
    seen = pdf_capture(monkeypatch, lambda: render(renderer, document, "pdf"))
    headings = list(document.headings)
    if "table" in seen:
        pdf = seen["table"]
    else:
        pdf, extra = card_columns(seen["cards"], headings)
        assert not extra, extra
    labels = [*labels, *(label for label, _ in document.metadata)]
    return headings, headers, sheets, pdf, labels


def case_participation(monkeypatch):
    """Participation: CSV and XLSX tables; its PDF and PNG are the chart."""
    value = participation()
    output = io.BytesIO()
    participation_csv(value, output)
    headers = csv_headers(output.getvalue())
    output = io.BytesIO()
    participation_xlsx(value, output)
    sheets, labels = xlsx_headers(output.getvalue())
    return headers[0], headers, sheets, None, labels


def case_additional_information(monkeypatch):
    """Additional information, with workflow history."""
    return shared_case(monkeypatch, information(count=2))


def case_family_directory(monkeypatch):
    """The Family-code directory (a table PDF)."""
    return shared_case(monkeypatch, directory([family()]), render_directory)


def case_postal_outreach(monkeypatch):
    """The postal mail merge: its PDF is address cards, checked by value."""
    document = directory([family()], postal=True)
    headers = csv_headers(render(render_directory, document, "csv"))
    sheets, labels = xlsx_headers(render(render_directory, document, "xlsx"))
    assert render(render_directory, document, "pdf").startswith(b"%PDF")
    values = dict(zip(document.headings, document.rows[0], strict=True))
    [block] = address_blocks(document)
    text = "\n".join(line for line, _ in block)
    for heading in ADDRESS_CARD_COLUMNS:
        assert str(values[heading]) in text, heading
    labels = [*labels, *(label for label, _ in document.metadata)]
    return list(document.headings), headers, sheets, None, labels


def case_ministry_summary(monkeypatch):
    """The Ministry request summary."""
    return shared_case(monkeypatch, ministry(action="summary", count=1))


def case_ministry_join(monkeypatch):
    """Prospective Ministry joiners."""
    return shared_case(monkeypatch, ministry(action="join", count=1))


def case_ministry_leave(monkeypatch):
    """Requested Ministry leavers."""
    return shared_case(monkeypatch, ministry(action="leave", count=1))


def case_packet(monkeypatch):
    """The multi-Ministry follow-up packet: one table per Ministry."""
    sections = [
        dict(duid=9, name="Choir", chairs=["Pat Lee"], rows=[member()]),
        dict(duid=10, name="Ushers", chairs=[], rows=[member(member_duid=7)]),
    ]
    document = packet(sections)
    headers = csv_headers(
        render(render_packet, document, "csv"), first=document.headings[0]
    )
    assert len(headers) == len(sections)
    sheets, labels = xlsx_headers(render(render_packet, document, "xlsx"))
    seen = pdf_capture(monkeypatch, lambda: render(render_packet, document, "pdf"))
    # A packet prints blank fields as write-in lines, so every column shows.
    pdf, extra = card_columns(seen["cards"], list(document.headings))
    assert not extra, extra
    labels = [*labels, *(label for label, _ in document.metadata)]
    return list(document.headings), headers, sheets, pdf, labels


def case_financial(monkeypatch):
    """Financial stewardship detail."""
    return shared_case(monkeypatch, financial([financial_row()]))


def case_family_test_names(monkeypatch):
    """The chosen-Family test names (``pk-admin test families-preview``)."""
    monkeypatch.setattr(family_test_names, "campaign_name", lambda request: "C")
    document = family_test_names.names_document(names_request([(12, "A")]))
    return shared_case(monkeypatch, document)


def case_response_list(monkeypatch):
    """Every response list, through ``list_file``."""
    results = []
    for key, spec in LISTS.items():
        rows = rows_of(key)

        def body(format, key=key, rows=rows):
            """The list's file in ``format``."""
            return list_file(
                LISTS[key],
                rows,
                NEW_YORK,
                format,
                details=details_of(key),
                as_of=details_of(key)[6][1],
            )

        headers = csv_headers(body("csv"))
        sheets, labels = xlsx_headers(body("xlsx"))
        seen = pdf_capture(monkeypatch, lambda body=body: body("pdf"))
        headings = [str(column.heading) for column in spec.columns]
        results.append((headings, headers, sheets, seen["table"], labels))
    return results


def case_census_changes(monkeypatch):
    """Pending census changes."""
    zone = ZoneInfo("America/Chicago")
    result = select(census_rows(), CensusQuery(), administrator=False)
    headers = csv_headers(census_csv(result, zone))
    sheets, labels = xlsx_headers(census_xlsx(result, zone))
    seen = pdf_capture(
        monkeypatch,
        lambda: census_pdf(result, zone, parish="P", campaign="C", as_of=SUBMITTED),
    )
    return headers[0], headers, sheets, seen["table"], labels


CASES = {
    name: globals()[f"case_{name}"]
    for name in (
        *(name for names in WORKER_CASES.values() for name in names),
        *PAGE_CASES,
    )
}


def test_every_export_kind_has_a_guard_case():
    """A report kind the export worker accepts cannot skip this guard."""
    assert worker_report_kinds() == set(WORKER_CASES)


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_format_carries_the_same_columns(monkeypatch, name):
    """CSV header == XLSX header == PDF columns, with no internal field.

    ``None`` for the PDF means it is not a table of these columns: the
    participation chart, and the postal labels (checked in their case).
    """
    results = CASES[name](monkeypatch)
    for headings, headers, sheets, pdf, labels in (
        results if isinstance(results, list) else [results]
    ):
        headings = list(headings)
        assert headers and all(header == headings for header in headers)
        assert sheets and all(sheet == headings for sheet in sheets)
        if pdf is not None:
            assert pdf == headings
        assert not INTERNAL_FIELDS & {*headings, *labels}
        # Plain names, as on the page: no snake_case program keys.
        assert not [
            heading for heading in headings if re.fullmatch(r"[a-z_]+", heading)
        ]
