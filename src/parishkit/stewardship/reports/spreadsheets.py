"""Typed, formula-free participation workbooks over the shared frozen document."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from openpyxl import Workbook

from .information_rendering import MONEY_FORMAT, excel_amount, xlsx_cell
from .participation import participation_table
from .xlsx_design import style_information, style_table


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
        _cell(sheet, 1, column, heading)
    for index, row in enumerate(rows, 2):
        for column, heading in enumerate(headings, 1):
            value = row[heading]
            if heading == "date":
                value = document.days[index - 2].local_date
            elif heading == "source_as_of" and value is not None:
                # In the stated display zone; xlsx_cell writes a native cell.
                value = datetime.fromisoformat(value).astimezone(
                    ZoneInfo(document.browser_timezone)
                )
            elif heading == "pledge_usd" and value is not None:
                # Test None explicitly: a zero Decimal is falsy but still a number.
                amount = excel_amount(value)
                value = value if amount is None else amount
            if isinstance(value, date):
                # Excel's built-in date (14) and date-time (22) formats.
                xlsx_cell(sheet, index, column, value)
                continue
            cell = _cell(
                sheet, index, column, "Unavailable" if value is None else value
            )
            if heading == "pledge_usd" and isinstance(value, Decimal):
                cell.number_format = MONEY_FORMAT
            elif type(value) is int:
                cell.number_format = "#,##0"
    style_table(sheet, title="Participation")
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
        _cell(metadata, index, 1, label)
        _cell(metadata, index, 2, value)
    style_information(metadata, title="Participation")
    book.save(output)
    book.close()
