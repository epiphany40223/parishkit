"""Complete mail-merge values and typed metadata without service startup."""

import csv
import io
from datetime import UTC, datetime

import pytest
from openpyxl import load_workbook

from parishkit.stewardship.reports.directory_documents import (
    HEADINGS,
    directory_document,
)
from parishkit.stewardship.reports.information_rendering import (
    information_lines,
    render_information,
)


def document(*, count=52, postal=True):
    """Exercise every directory column, unknown addresses and hostile literal text."""
    moment = datetime(2026, 10, 2, 13, tzinfo=UTC)
    item = dict(
        family_name="=Sample Family",
        family_duid=12345,
        code="ABCDEFGH",
        email_eligible=True,
        email_deliverable=False,
        reason="provider_refused",
        responded=True,
        envelope="00123",
        heads=[{"name": "First Head"}, {"name": "Second Head"}],
        phones=[dict(owner="Member", kind="mobile", value="202-555-0123")],
        address=dict(
            primaryAddress1="1 Sample St",
            primaryAddress2=None,
            primaryCity="Town",
            primaryState="KY",
            primaryPostalCode="40000",
            primaryZipPlus="1234",
        ),
    )
    payload = dict(
        metadata=dict(
            name="Annual campaign",
            id="campaign",
            source_id="source",
            source_generation=1234,
            source_as_of=moment.isoformat(),
        ),
        total=count,
        rows=[item | {"family_duid": 12345 + index} for index in range(count)],
    )
    return directory_document(
        payload,
        {"filters": {"search": "Sample"}, "postal": postal, "exact": False},
        parish_name="Sample Parish",
        captured_at=moment,
        requested_at=moment,
        timezone="America/Detroit",
    )


def test_directory_csv_keeps_every_matching_mail_merge_row_and_metadata():
    """One UI page cannot truncate export rows, addresses, codes or leading zeros."""
    result = io.BytesIO()
    render_information(document(), result, format="csv")
    assert b"\r\n" in result.getvalue()
    rows = list(csv.DictReader(io.StringIO(result.getvalue().decode())))
    assert rows[0]["Record"] == "Report metadata"
    families = [row for row in rows if row["Record"] == "Family"]
    assert len(families) == 52 and families[-1]["Family DUID"] == "12396"
    for row in families:
        assert row["Family"] == "'=Sample Family"
        assert row["Manual code"] == "ABCDEFGH" and row["Envelope number"] == "00123"
        assert row["Primary address line 2"] == ""
        assert (
            row["Primary postal code"] == "40000"
            and row["Primary ZIP extension"] == "1234"
        )
        assert (
            row["Separate mailing address"]
            == row["Separate home address"]
            == "Unavailable"
        )
        assert row["Requested at"] == "2026-10-02T09:00:00-04:00"
        assert row["Source generation"] == "1,234"
    assert b"opaque" not in result.getvalue() and b"token" not in result.getvalue()


def test_directory_xlsx_and_pdf_share_complete_columns_and_report_identity():
    """Detached values drive literal spreadsheet cells and complete PDF text."""
    report = document(count=1, postal=False)
    output = io.BytesIO()
    render_information(report, output, format="xlsx")
    book = load_workbook(output)
    sheet = book["Families"]
    assert tuple(cell.value for cell in sheet[1]) == HEADINGS
    assert sheet["B2"].value == "=Sample Family" and sheet["B2"].data_type == "s"
    assert sheet["D2"].value == "ABCDEFGH" and sheet["I2"].value == "00123"
    assert sheet.freeze_panes == "A2" and sheet.print_title_rows == "$1:$1"
    book.close()
    lines = "\n".join(information_lines(report))
    assert "Manual code: ABCDEFGH" in lines
    assert "Primary ZIP extension: 1234" in lines and "Family-code directory" in lines
    assert "Second Head" in lines and "202-555-0123" in lines
    output = io.BytesIO()
    assert render_information(report, output, format="pdf") > 0
    assert output.getvalue().startswith(b"%PDF")


@pytest.mark.parametrize("format", ["csv", "xlsx", "pdf"])
def test_empty_directory_exports_preserve_provenance(format):
    """An empty exact-code/postal selection is a labeled report, not an error."""
    report = document(count=0)
    assert report.item_count == 0
    output = io.BytesIO()
    render_information(report, output, format=format)
    assert output.getvalue()
    assert dict(report.metadata)["Matching Families"] == "0"
