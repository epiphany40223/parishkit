"""Complete, safe information exports without database or provider startup."""

import csv
import io
from datetime import UTC, datetime

import pytest
from openpyxl import load_workbook

from parishkit.stewardship.reports.information_documents import (
    HEADINGS,
    information_document,
)
from parishkit.stewardship.reports.information_rendering import (
    information_csv,
    information_lines,
    information_pdf,
    information_xlsx,
)


def document(*, count=1, history=True):
    """Use hostile literal values and enough text to cross a PDF page boundary."""
    instant = "2026-10-02T12:00:00+00:00"
    item = dict(
        id="item",
        version=3,
        family_name="=Sample <Family>",
        family_duid=12345,
        submitted_at=instant,
        disposition="current_actionable",
        replacement_id=None,
        text="First line\n\n" + "x" * 4200 + "\nFINAL TEXT MARKER",
        follow_up_needed=True,
        followed_up_at=instant,
        completed_by="staff@example.org",
        notes="+Literal notes",
        previously_reported=True,
        correction_resolved=False,
        history=[
            dict(
                version=2,
                follow_up_needed=True,
                followed_up_at=None,
                notes="@Older notes",
                created_at=instant,
                actor_label="staff@example.org",
            )
        ],
    )
    return information_document(
        dict(
            metadata=dict(
                id="campaign",
                name="Sample campaign",
                source_id="source",
                source_generation=1234,
                source_as_of=instant,
                observed_at=instant,
                timezone="America/Detroit",
            ),
            total=count,
            rows=[item] * count,
        ),
        dict(filters={"search": "private"}, history=history),
        parish_name="Sample Parish",
        requested_at=datetime(2026, 10, 2, 13, tzinfo=UTC),
        timezone="America/Detroit",
    )


@pytest.mark.parametrize("history", [True, False])
def test_csv_full_results_and_formula_safety(history):
    """More than a UI page, complete text/history, immutable local-time metadata."""
    report = document(count=51, history=history)
    stream = io.BytesIO()
    information_csv(report, stream)
    assert not stream.closed and b"\r\n" in stream.getvalue()
    rows = list(csv.DictReader(io.StringIO(stream.getvalue().decode())))
    assert rows[0]["Record"] == "Report metadata"
    assert len(rows) == 1 + 51 * (2 if history else 1)
    items = [row for row in rows if row["Record"] == "Item"]
    assert all(row["Family"] == "'=Sample <Family>" for row in items)
    assert all(row["Staff notes"] == "'+Literal notes" for row in items)
    assert items[-1]["Submitted text"].endswith("FINAL TEXT MARKER")
    assert items[-1]["Requested at"] == "2026-10-02T09:00:00-04:00"
    assert len(report.rows[0]) == len(HEADINGS)


def test_xlsx_values_are_literal_complete_and_structured():
    """Test ParishKit's cell construction, not the spreadsheet library itself."""
    stream = io.BytesIO()
    information_xlsx(document(), stream)
    book = load_workbook(io.BytesIO(stream.getvalue()))
    try:
        sheet = book["Information"]
        assert sheet["D2"].value == "=Sample <Family>" and sheet["D2"].data_type == "s"
        assert sheet["I2"].value.endswith("FINAL TEXT MARKER")
        assert sheet["M3"].value == "@Older notes" and sheet["M3"].data_type == "s"
        assert sheet.freeze_panes == "A2" and sheet.print_title_rows == "$1:$1"
        assert sheet.auto_filter.ref == "A1:Q3"
        assert book["Report information"]["B9"].value == "2026-10-02T09:00:00-04:00"
    finally:
        book.close()


def test_pdf_pagination_preserves_all_long_text_and_history():
    """The exact line plan drawn by PDF preserves paragraphs, tails and long words."""
    report = document()
    lines = tuple(information_lines(report))
    assert any("FINAL TEXT MARKER" in line for line in lines)
    assert (
        sum(len(line.strip()) for line in lines if set(line.strip()) == {"x"}) == 4200
    )
    assert any("@Older notes" in line for line in lines)
    assert all(len(line) <= 108 for line in lines)
    stream = io.BytesIO()
    pages = information_pdf(report, stream)
    assert pages > 1 and stream.getvalue().startswith(b"%PDF")
    assert not stream.closed


def test_empty_reports_still_include_source_and_request_provenance():
    """No-result exports remain self-identifying without inventing an item row."""
    report = document(count=0)
    stream = io.BytesIO()
    information_csv(report, stream)
    rows = list(csv.DictReader(io.StringIO(stream.getvalue().decode())))
    assert len(rows) == 1 and rows[0]["Matching items"] == "0"
    assert rows[0]["Source reference"] == "source"
    assert rows[0]["Requested at"] == "2026-10-02T09:00:00-04:00"
    assert report.item_count == 0 and not report.rows
