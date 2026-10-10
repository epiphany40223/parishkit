"""CSV, XLSX and PDF files for the directory exports (directory_documents).

CSV is exactly one header row plus one row per Family, so it opens cleanly
in a spreadsheet or a word processor's mail merge. XLSX has the same sheet
plus the shared "Report information" sheet. PDF puts the report details in
each page's header and footer: a table for the Family-code directory, and
one address block per Family for the postal mail merge.
"""

import csv
import io

from parishkit.stewardship.web import dates
from parishkit.stewardship.web.exports import csv_cell

from .directory_documents import HEAD_EMAILS_DETAIL, UNADDRESSED_DETAIL
from .information_rendering import information_xlsx

# Relative column widths of the Family-code table. Family holds the surname
# and the heads of household; long cells wrap within their column. Phone
# numbers is wide enough that "Name (kind): +1 (555) 555-0123" fits a line.
COLUMN_WEIGHTS = {
    "Family": 21,
    "ParishSoft DUID": 8,
    "Family code": 9,
    "Phone numbers": 31,
    "Family head emails": 31,
}
# List cells whose entries ("; "-separated) each start a new line in the PDF.
LIST_COLUMNS = frozenset({"Phone numbers", "Family head emails"})


def directory_csv(document, output):
    """One header row and one row per Family; no metadata row or record column."""
    wrapper = io.TextIOWrapper(output, encoding="utf-8", newline="", write_through=True)
    try:
        writer = csv.writer(wrapper, lineterminator="\r\n")
        writer.writerow(document.headings)
        for row in document.rows:
            writer.writerow(tuple(map(csv_cell, row)))
        wrapper.flush()
    finally:
        wrapper.detach()


def directory_table(document):
    """The Family-code table's columns, sized for the landscape page."""
    from .pdf_design import Table

    return Table.weighted(
        document.headings, [COLUMN_WEIGHTS[name] for name in document.headings]
    )


def table_rows(document):
    """The PDF table's rows: each phone and each head's emails on its own line.

    Only the separating space becomes a line break, so every character of
    the cell is still drawn; CSV and XLSX keep the one-line cell.
    """
    lists = [heading in LIST_COLUMNS for heading in document.headings]
    for row in document.rows:
        yield [
            value.replace("; ", ";\n") if listed else value
            for value, listed in zip(row, lists, strict=True)
        ]


def address_blocks(document):
    """One mailing-label style card per Family for the postal PDF.

    Each card is (text, emphasis) pairs: the addressee (or a plain warning
    when there is no usable mailing address), the address, and a muted
    follow-up line naming the Family, its DUID and its Family code.
    """
    names = document.headings
    for row in document.rows:
        values = dict(zip(names, row, strict=True))
        city = ", ".join(filter(None, (values["City"], values["State"])))
        # A blank Addressee means no usable mailing address (see
        # directory_document); say so rather than print a bare Family line.
        addressee = values["Addressee"]
        lines = [
            (addressee, "title")
            if addressee
            else ("No usable mailing address", "warning"),
            *((values[f"Address line {index}"], "") for index in (1, 2, 3)),
            (" ".join(filter(None, (city, values["ZIP"]))), ""),
            (
                f"Family: {values['Family']} · ParishSoft DUID "
                f"{values['ParishSoft DUID']} · Family code "
                f"{values['Family code'] or 'unavailable'}",
                "muted",
            ),
        ]
        yield tuple((text, emphasis) for text, emphasis in lines if text)


def directory_frame(document):
    """The shared page frame with the directory's counts and filters."""
    from .pdf_design import PdfFrame

    details = dict(document.metadata)
    counts = f"{details['Families in this file']} Families in this file"
    if document.postal:
        counts += (
            f" · {details[UNADDRESSED_DETAIL]} with no usable mailing address "
            "(address left blank)"
        )
    lines = [f"{counts} · Filters: {details['Filters applied']}"]
    # Head emails read from newer ParishSoft data than the capture.
    if HEAD_EMAILS_DETAIL in details:
        lines.append(
            f"{HEAD_EMAILS_DETAIL} {dates.display_text(details[HEAD_EMAILS_DETAIL])}"
        )
    return PdfFrame.for_document(document, details=lines, stamp_key="Captured at")


def directory_pdf(document, output):
    """A zebra table (Family codes) or address cards (postal), framed."""
    from . import pdf_design

    frame = directory_frame(document)
    if document.postal:
        cards = [pdf_design.card_lines(block) for block in address_blocks(document)]
        pages = list(pdf_design.card_pages(cards, frame))

        def draw(canvas, page):
            """One page of address cards."""
            pdf_design.draw_cards(canvas, page, frame)

    else:
        table = directory_table(document)
        pages = list(pdf_design.table_pages(table, table_rows(document), frame))
        draw = pdf_design.draw_table(table, frame)
    return frame.write(
        output, pages, len(pages), draw, requested_at=document.requested_at
    )


def render_directory(document, output, *, format):
    """Only the three compiled directory formats are executable."""
    renderers = {"csv": directory_csv, "xlsx": information_xlsx, "pdf": directory_pdf}
    try:
        renderer = renderers[format]
    except (KeyError, TypeError) as error:
        raise ValueError("Unsupported directory export format.") from error
    return renderer(document, output)
