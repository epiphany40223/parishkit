"""Safe complete-text exports shared by information and Family-directory reports."""

import csv
import io
from functools import cache
from itertools import chain, islice
from pathlib import Path
from textwrap import wrap
from unicodedata import category

from parishkit.stewardship.web.exports import csv_cell

PAGE_LINES = 34
FORMAT_NOTE = (
    "Unsupported characters use Unicode escapes (\\uXXXX or \\UXXXXXXXX); "
    "literal backslashes are doubled. CSV retains the original Unicode text."
)


def visible_text(value, *, supported=None):
    """Reversibly represent format/font exclusions; never drop a private character.

    XLSX permits XML 1.0 characters. PDF uses the exact bundled font's character
    map and escapes controls as well, avoiding missing glyphs and invisible
    terminal controls. Escaping backslashes makes the notation unambiguous.
    """
    result = []
    for character in value:
        code = ord(character)
        allowed = (
            code in {9, 10, 13}
            or 0x20 <= code <= 0xD7FF
            or 0xE000 <= code <= 0xFFFD
            or 0x10000 <= code <= 0x10FFFF
        )
        if supported is not None:
            allowed = code == 10 or (
                category(character) not in {"Cc", "Cf"} and code in supported
            )
        if character == "\\":
            result.append("\\\\")
        elif allowed:
            result.append(character)
        else:
            result.append(f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}")
    return "".join(result)


@cache
def pdf_font():
    """Use the same pinned bundled font for glyph validation and actual drawing."""
    from matplotlib import get_data_path
    from matplotlib.ft2font import FT2Font

    path = Path(get_data_path()) / "fonts/ttf/DejaVuSansMono.ttf"
    return str(path), frozenset(FT2Font(str(path)).get_charmap())


def information_csv(document, output):
    """Emit a typed metadata row even for an empty result; never lose provenance."""
    wrapper = io.TextIOWrapper(output, encoding="utf-8", newline="", write_through=True)
    try:
        writer = csv.writer(wrapper, lineterminator="\r\n")
        writer.writerow((*document.headings, *(key for key, _ in document.metadata)))
        trailer = tuple(csv_cell(value) for _, value in document.metadata)
        writer.writerow(
            ("Report metadata", *("" for _ in document.headings[1:]), *trailer)
        )
        for row in document.rows:
            writer.writerow((*map(csv_cell, row), *trailer))
        wrapper.flush()
    finally:
        wrapper.detach()


def information_xlsx(document, output):
    """Literal cells retain complete values; metadata survives empty reports too."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    book = Workbook()
    try:
        sheet = book.active
        sheet.title = document.sheet_name
        for row_index, values in enumerate(
            chain((document.headings,), document.rows), 1
        ):
            for column, value in enumerate(values, 1):
                cell = sheet.cell(row_index, column, visible_text(value))
                cell.data_type = "s"
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                if row_index == 1:
                    cell.font = Font(bold=True)
        for index, heading in enumerate(document.headings, 1):
            sheet.column_dimensions[get_column_letter(index)].width = (
                70 if heading in {"Submitted text", "Staff notes"} else 28
            )
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.print_title_rows = "1:1"
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth = 1
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.oddFooter.center.text = "Page &P of &N"
        metadata = book.create_sheet("Report information")
        for index, (key, value) in enumerate(
            chain(document.metadata, (("Text representation", FORMAT_NOTE),)), 1
        ):
            metadata.cell(index, 1, key).font = Font(bold=True)
            cell = metadata.cell(index, 2, visible_text(value))
            cell.data_type = "s"
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        metadata.column_dimensions["A"].width = 32
        metadata.column_dimensions["B"].width = 90
        book.save(output)
    finally:
        book.close()


def record_lines(records, *, width=108):
    """Lay out label/value records without ellipsis, one blank line after each.

    Blank lines and long words survive; nothing is truncated. Shared by every
    field/value PDF so they wrap and escape identically.
    """
    supported = pdf_font()[1]
    for record in records:
        for label, value in record:
            prefix = label + ": "
            for index, paragraph in enumerate(
                visible_text(value, supported=supported).split("\n")
            ):
                lines = wrap(
                    paragraph,
                    width=width - len(prefix),
                    expand_tabs=True,
                    replace_whitespace=False,
                    drop_whitespace=False,
                    break_long_words=True,
                    break_on_hyphens=False,
                ) or [""]
                for offset, line in enumerate(lines):
                    yield (
                        prefix if index == 0 and offset == 0 else " " * len(prefix)
                    ) + line
        yield ""


def information_lines(document, *, width=108):
    """Lay out every value without ellipsis, including blank lines/long words.

    A two-column field/value layout avoids compressing seventeen columns into
    unreadable widths. The PDF owner repeats its column headings on every page.
    """
    return record_lines(
        chain(
            (document.metadata, (("Text representation", FORMAT_NOTE),)),
            (zip(document.headings, row, strict=True) for row in document.rows),
        ),
        width=width,
    )


def information_pdf(document, output):
    """Paginate before drawing so no complete-text cell is clipped at a page end."""

    # Count without retaining wrapped strings, then stream one bounded page at
    # a time. Both passes use the same detached document and bundled font.
    page_count = -(-sum(1 for _ in information_lines(document)) // PAGE_LINES)
    lines = iter(information_lines(document))
    return write_pages(
        document,
        output,
        (tuple(islice(lines, PAGE_LINES)) for _ in range(page_count)),
        page_count,
    )


def write_pages(document, output, pages, page_count):
    """Draw already paginated lines, one bounded figure at a time."""
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties

    from .charts import rendering_style

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
                figure.text(0.05, 0.90, "Field / complete value", fontsize=10)
                for index, line in enumerate(page):
                    figure.text(
                        0.05,
                        0.865 - index * 0.022,
                        line,
                        fontsize=9,
                        fontproperties=font,
                        va="top",
                    )
                figure.text(
                    0.95,
                    0.04,
                    f"Page {number:,} of {page_count:,}",
                    ha="right",
                    fontsize=9,
                )
                pdf.savefig(figure)
            finally:
                figure.clear()
    return page_count


def render_information(document, output, *, format):
    """Only three explicitly compiled report formats are executable."""
    renderers = {
        "csv": information_csv,
        "xlsx": information_xlsx,
        "pdf": information_pdf,
    }
    try:
        renderer = renderers[format]
    except (KeyError, TypeError) as error:
        raise ValueError("Unsupported information export format.") from error
    return renderer(document, output)
