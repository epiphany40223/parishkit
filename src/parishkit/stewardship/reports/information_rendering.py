"""Safe complete-text exports shared by information and Family-directory reports."""

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from itertools import chain
from unicodedata import category

from parishkit.stewardship.web import dates
from parishkit.stewardship.web.exports import csv_cell

from .money import MoneyAmount

# Excel's number format for exact USD: "-$50.00" for a negative amount, the
# same text the reports display, while the cell stays a summable number.
MONEY_FORMAT = '"$"#,##0.00'
# Excel keeps only 15 significant digits; a longer amount would round silently.
EXCEL_DIGITS = 15
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


def plain(value):
    """Money as its report text for PDF; every other value unchanged.

    The XLSX writer keeps money numeric (see ``xlsx_cell``) and CSV writes
    its canonical amount (``csv_cell_value``); PDF says exactly what the page says.
    """
    return value.display if isinstance(value, MoneyAmount) else value


def csv_cell_value(value):
    """One CSV cell: known money as its canonical amount, else the guarded text.

    A known amount is the plain signed decimal (``1200.00``, ``-50.00``,
    ``0.00``): no dollar sign, thousands separator or formula-guard
    apostrophe, so a spreadsheet reads it as a number and a script parses
    it without stripping text (#388 L5). Such a value is only an optional
    minus sign, digits, a point and two digits, so it cannot be a formula
    and needs no neutralizing. Unavailable money stays the word, and every
    other value goes through ``csv_cell`` as before.
    """
    if isinstance(value, MoneyAmount) and value.available:
        return value.canonical
    return csv_cell(plain(value))


def excel_amount(value):
    """An exact Decimal Excel can hold, or None when it would lose a digit."""
    amount = Decimal(value)
    return amount if len(amount.as_tuple().digits) <= EXCEL_DIGITS else None


def xlsx_cell(sheet, row, column, value):
    """Write one cell: a native date/timestamp or amount, or literal text.

    Timestamps arrive already converted to the export's stated display time
    zone; Excel datetimes are naive, so the zone is dropped only here. Excel's
    built-in formats 14 and 22 follow each viewer's own regional settings.

    Known money is a number cell with a dollar format, so staff can sum it.
    The value is built exactly from whole cents as a Decimal, but a
    spreadsheet number is an IEEE double: openpyxl saves it with "%.16g", so
    $97.57 may be stored as 97.56999999999999. Within 15 significant digits
    that is still accurate to the cent when opened, displayed or summed.
    Unavailable money stays the word, never zero, and an amount beyond
    those 15 digits stays its exact text rather than rounding. A whole
    number (an ``int``, never a ``bool``) is a number cell, such as a count.
    None is a truly empty cell. Text is never a formula.
    """
    from openpyxl.styles.numbers import BUILTIN_FORMATS

    if value is None or type(value) is int:
        return sheet.cell(row, column, value)

    if isinstance(value, MoneyAmount):
        amount = excel_amount(value.canonical) if value.available else None
        if amount is not None:
            cell = sheet.cell(row, column, amount)
            cell.number_format = MONEY_FORMAT
            return cell
        value = value.display
    if isinstance(value, date):
        timestamp = isinstance(value, datetime)
        # Set the format before the value: openpyxl otherwise registers its own
        # custom ISO pattern for the value, which would stay in the file.
        cell = sheet.cell(row, column)
        cell.number_format = BUILTIN_FORMATS[
            dates.XLSX_DATETIME if timestamp else dates.XLSX_DATE
        ]
        cell.value = value.replace(tzinfo=None) if timestamp else value
        return cell
    cell = sheet.cell(row, column, visible_text(dates.display_text(value)))
    cell.data_type = "s"
    return cell


def information_csv(document, output):
    """Emit a typed metadata row even for an empty result; never lose provenance."""
    wrapper = io.TextIOWrapper(output, encoding="utf-8", newline="", write_through=True)
    try:
        writer = csv.writer(wrapper, lineterminator="\r\n")
        writer.writerow((*document.headings, *(key for key, _ in document.metadata)))
        trailer = tuple(csv_cell_value(value) for _, value in document.metadata)
        writer.writerow(
            ("Report metadata", *("" for _ in document.headings[1:]), *trailer)
        )
        for row in document.rows:
            writer.writerow((*(csv_cell_value(value) for value in row), *trailer))
        wrapper.flush()
    finally:
        wrapper.detach()


def information_xlsx(document, output):
    """Literal cells retain complete values; metadata survives empty reports too."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment

    from .xlsx_design import style_information, style_table

    book = Workbook()
    try:
        sheet = book.active
        sheet.title = document.sheet_name
        for row_index, values in enumerate(
            chain((document.headings,), document.rows), 1
        ):
            for column, value in enumerate(values, 1):
                cell = xlsx_cell(sheet, row_index, column, value)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        # Long free text gets a wide column; a directory's Family column
        # stays in view while scrolling across its contact columns.
        style_table(
            sheet,
            widths={"Submitted text": 70, "Staff notes": 70},
            freeze_first_column=document.headings[0] == "Family",
            title=document.title,
        )
        metadata = book.create_sheet("Report information")
        for index, (key, value) in enumerate(
            chain(document.metadata, (("Text representation", FORMAT_NOTE),)), 1
        ):
            xlsx_cell(metadata, index, 1, key)
            xlsx_cell(metadata, index, 2, value)
        style_information(metadata, title=document.title)
        book.save(output)
    finally:
        book.close()


def information_records(document):
    """The report-information card, then one card per record (lazily).

    A field/value card avoids compressing seventeen columns into unreadable
    widths. The PDF is the readable view: it leaves out internal references
    and blank fields, which CSV and XLSX keep (``pdf_design.row_records``).
    """
    from .pdf_design import report_record, row_records

    return chain(
        (report_record(document.metadata, values=chain.from_iterable(document.rows)),),
        row_records(document.headings, document.rows),
    )


def information_pdf(document, output):
    """Paginate before drawing so every page states its position.

    Count without retaining wrapped lines, then stream one bounded page at a
    time; both passes lay out the same detached document identically.
    """
    from .pdf_design import PdfFrame, write_records

    return write_records(
        output,
        PdfFrame.for_document(document),
        lambda: information_records(document),
        requested_at=document.requested_at,
    )


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
