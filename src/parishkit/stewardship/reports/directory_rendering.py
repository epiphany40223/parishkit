"""CSV, XLSX and PDF files for the directory exports (directory_documents).

CSV is exactly one header row plus one row per Family, so it opens cleanly
in a spreadsheet or a word processor's mail merge. XLSX has the same sheet
plus the shared "Report information" sheet. PDF puts the report details in
each page's header and footer: a table for the Family-code directory, and
one address block per Family for the postal mail merge.
"""

import csv
import io
from textwrap import wrap

from parishkit.stewardship.web.exports import csv_cell

from .information_rendering import information_xlsx, pdf_font, visible_text

PAGE_LINES = 32
LINE_WIDTH = 124
# Character widths of the Family-code table's columns (monospaced PDF text).
# Family holds the surname and the heads of household on one line.
COLUMN_WIDTHS = {
    "Family": 68,
    "ParishSoft DUID": 15,
    "Family code": 14,
    "Phone numbers": 45,
}


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


def _cell_lines(value, width):
    """Wrap one cell to its column width without dropping any text."""
    text = visible_text(value, supported=pdf_font()[1])
    return wrap(text, width=width, break_long_words=True) or [""]


def table_lines(document):
    """Fixed-width table rows (with wrapped cells) for the Family-code PDF."""
    widths = [COLUMN_WIDTHS[heading] for heading in document.headings]
    for row in document.rows:
        cells = [
            _cell_lines(value, width) for value, width in zip(row, widths, strict=True)
        ]
        height = max(len(cell) for cell in cells)
        yield tuple(
            "  ".join(
                (cell[index] if index < len(cell) else "").ljust(width)
                for cell, width in zip(cells, widths, strict=True)
            ).rstrip()
            for index in range(height)
        )


def address_blocks(document):
    """One mailing-label style block per Family for the postal PDF."""
    names = document.headings
    for row in document.rows:
        values = dict(zip(names, row, strict=True))
        city = ", ".join(filter(None, (values["City"], values["State"])))
        lines = [
            values["Addressee"],
            *(values[f"Address line {index}"] for index in (1, 2, 3)),
            " ".join(filter(None, (city, values["ZIP"]))),
            f"Family: {values['Family']} · ParishSoft DUID {values['ParishSoft DUID']}"
            f" · Family code {values['Family code'] or 'unavailable'}",
        ]
        yield tuple(
            line for text in lines if text for line in _cell_lines(text, LINE_WIDTH)
        )


def _pages(blocks):
    """Fill pages with whole blocks (a table row or an address block).

    A block taller than a page (a Family with very many phone numbers) is
    split across pages rather than drawn past the footer.
    """
    page = []
    for whole in blocks:
        for start in range(0, max(len(whole), 1), PAGE_LINES - 1):
            yield from _place(page, whole[start : start + PAGE_LINES - 1])
    if page or not blocks:
        yield page


def _place(page, block):
    """Add one block to the page being filled, first flushing a full page."""
    if page and len(page) + len(block) > PAGE_LINES:
        yield list(page)
        page.clear()
    page.extend(block)
    page.append("")


def directory_pdf(document, output):
    """Draw every page with the report details in its header and footer."""
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties

    from .charts import rendering_style

    blocks = list(
        address_blocks(document) if document.postal else table_lines(document)
    )
    pages = list(_pages(blocks))
    details = dict(document.metadata)
    heading = (
        None
        if document.postal
        else "  ".join(
            name.ljust(COLUMN_WIDTHS[name]) for name in document.headings
        ).rstrip()
    )
    subtitle = " · ".join(
        (details["Parish"], details["Campaign"], f"Captured {details['Captured at']}")
    )
    counts = f"{details['Families in this file']} Families in this file"
    if document.postal:
        counts += (
            f"; {details['Not in this file: no usable mailing address']} "
            "without a usable mailing address are not included"
        )
    font = FontProperties(fname=pdf_font()[0])
    with (
        rendering_style(),
        PdfPages(
            output,
            metadata={
                "Title": document.title,
                "CreationDate": document.requested_at,
                "ModDate": document.requested_at,
            },
        ) as pdf,
    ):
        for number, page in enumerate(pages, 1):
            figure = Figure(figsize=(11, 8.5), facecolor="white")
            try:
                figure.text(0.05, 0.95, document.title, fontsize=13)
                figure.text(0.05, 0.925, visible_text(subtitle), fontsize=9)
                figure.text(
                    0.05,
                    0.905,
                    visible_text(f"{counts}. Filters: {details['Filters applied']}"),
                    fontsize=9,
                )
                top = 0.87
                if heading is not None:
                    figure.text(
                        0.05,
                        top,
                        heading,
                        fontsize=9,
                        fontproperties=font,
                        va="top",
                        weight="bold",
                    )
                    top -= 0.03
                for index, line in enumerate(page):
                    figure.text(
                        0.05,
                        top - index * 0.024,
                        line,
                        fontsize=9,
                        fontproperties=font,
                        va="top",
                    )
                figure.text(0.05, 0.04, details["Privacy"], fontsize=9)
                if "Testing mode" in details:
                    figure.text(
                        0.05, 0.065, visible_text(details["Testing mode"]), fontsize=8
                    )
                figure.text(
                    0.95,
                    0.04,
                    f"Page {number:,} of {len(pages):,}",
                    ha="right",
                    fontsize=9,
                )
                pdf.savefig(figure)
            finally:
                figure.clear()
    return len(pages)


def render_directory(document, output, *, format):
    """Only the three compiled directory formats are executable."""
    renderers = {"csv": directory_csv, "xlsx": information_xlsx, "pdf": directory_pdf}
    try:
        renderer = renderers[format]
    except (KeyError, TypeError) as error:
        raise ValueError("Unsupported directory export format.") from error
    return renderer(document, output)
