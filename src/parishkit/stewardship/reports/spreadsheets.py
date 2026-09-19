"""Typed, formula-free participation workbooks over the shared frozen document."""

from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .participation import participation_table


def _cell(sheet, row, column, value):
    """Keep text literal even when a parish-controlled label starts with '='."""
    cell = sheet.cell(row, column, value)
    if isinstance(value, str):
        cell.data_type = "s"
    return cell


def participation_xlsx(document, output):
    """Export every daily row, preserving unavailable versus zero and provenance.

    Excel only preserves 15 significant decimal digits. Very large aggregate
    pledges therefore use their exact decimal text instead of silently rounding.
    Ordinary amounts are numeric cells with a dollar number format.
    """
    rows = participation_table(document)
    book = Workbook()
    sheet = book.active
    sheet.title = "Participation"
    headings = [
        "date",
        "scope",
        "first_responses",
        "cumulative_responses",
        "cohort_denominator",
        "participation",
        "source_generation",
        "source_as_of",
    ]
    if document.financial_enabled:
        headings.append("pledge_usd")
    for column, heading in enumerate(headings, 1):
        _cell(sheet, 1, column, heading).font = Font(bold=True)
        sheet.column_dimensions[get_column_letter(column)].width = 28
    for index, row in enumerate(rows, 2):
        for column, heading in enumerate(headings, 1):
            value = row[heading]
            if heading == "date":
                value = document.days[index - 2].local_date
            elif heading == "pledge_usd" and value is not None:
                amount = Decimal(value)
                value = amount if len(amount.as_tuple().digits) <= 15 else value
            cell = _cell(
                sheet, index, column, "Unavailable" if value is None else value
            )
            if heading == "date":
                cell.number_format = "yyyy-mm-dd"
            elif heading == "pledge_usd" and isinstance(value, Decimal):
                cell.number_format = '"$"#,##0.00'
            elif type(value) is int:
                cell.number_format = "#,##0"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    sheet.print_title_rows = "1:1"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.oddFooter.center.text = "Page &P of &N"
    metadata = book.create_sheet("Report information")
    for index, (label, value) in enumerate(
        (
            ("Parish", document.parish_name),
            ("Campaign", document.campaign_name),
            ("Campaign UUID", str(document.campaign_id)),
            ("Fact generation UUID", str(document.fact_set_id)),
            ("Population", document.scope_label),
            ("Campaign timezone", document.campaign_timezone),
            ("Display timezone", document.browser_timezone),
            ("Source and original request", document.as_of_label),
            ("Submission cutoff", document.submission_watermark),
            ("Daily rows", len(rows)),
            ("Missing observations", "Unavailable is not zero."),
            (
                "Large amounts",
                "Amounts exceeding Excel's 15-digit precision are exact text.",
            ),
        ),
        1,
    ):
        _cell(metadata, index, 1, label).font = Font(bold=True)
        _cell(metadata, index, 2, value)
    metadata.column_dimensions["A"].width = 32
    metadata.column_dimensions["B"].width = 85
    book.save(output)
    book.close()
