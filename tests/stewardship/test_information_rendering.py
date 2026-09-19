"""Complete, safe information exports without database or provider startup."""

import csv
import io
import warnings
from dataclasses import replace
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
    visible_text,
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


def test_format_exclusions_have_lossless_visible_notation():
    """XML controls and unavailable glyphs cannot poison an immutable export."""
    value = "Éλληνικά 中文 🙂\x0b\x1b\ufffe literal \\u000b"
    report = document()
    row = list(report.rows[0])
    row[8] = value
    report = replace(report, rows=(tuple(row),))
    expected = "Éλληνικά 中文 🙂\\u000b\\u001b\\ufffe literal \\\\u000b"
    assert visible_text(value) == expected
    stream = io.BytesIO()
    information_xlsx(report, stream)
    book = load_workbook(io.BytesIO(stream.getvalue()))
    try:
        assert book["Information"]["I2"].value == expected
        assert "Unsupported characters" in book["Report information"]["B17"].value
    finally:
        book.close()
    lines = "\n".join(information_lines(report))
    assert "Éλληνικά" in lines
    assert "\\u4e2d\\u6587" in lines and "\\U0001f642" in lines
    assert "\\u000b\\u001b\\ufffe" in lines and "\\\\u000b" in lines
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        information_pdf(report, io.BytesIO())
    assert not any("Glyph" in str(w.message) for w in caught)


def test_pdf_draws_pages_without_accumulating_wrapped_report(monkeypatch):
    """After the counting pass, drawing consumes at most one page ahead."""
    from matplotlib.backends.backend_pdf import PdfPages

    from parishkit.stewardship.reports import information_rendering

    passes, consumed, saved = 0, 0, 0
    savefig = PdfPages.savefig

    def lines(document):
        """Expose line consumption without relying on platform memory accounting."""
        nonlocal passes, consumed
        passes += 1
        for index in range(80):
            if passes == 2:
                consumed += 1
            yield f"Line {index}"

    def save_page(pdf, figure):
        """A whole-report list would consume all 80 before the first save."""
        nonlocal saved
        saved += 1
        assert consumed == min(saved * 34, 80)
        return savefig(pdf, figure)

    monkeypatch.setattr(information_rendering, "information_lines", lines)
    monkeypatch.setattr(PdfPages, "savefig", save_page)
    assert information_pdf(document(), io.BytesIO()) == saved == 3
    assert passes == 2
