"""Detached financial export documents render every format from one capture."""

import csv
import io
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from parishkit.stewardship.reports.financial import FREQUENCY_LABELS, FinancialQuery
from parishkit.stewardship.reports.financial_documents import (
    HEADINGS,
    UNPROVEN,
    financial_document,
    headings,
)
from parishkit.stewardship.reports.information_rendering import (
    render_information,
    visible_text,
)
from parishkit.stewardship.reports.money import MoneyAmount
from parishkit.stewardship.source.snapshot_names import FAMILY_NAMES_FULL
from parishkit.stewardship.web.dates import Span, csv_text

MOMENT = datetime(2026, 9, 19, 15, 4, tzinfo=UTC)
PARAMETERS = {"filters": FinancialQuery().form_values(), "proof": None}

NEW_YORK = ZoneInfo("America/New_York")


def row(**changes):
    """One shaped page row, as the read model hands it to the template."""
    return {
        "id": str(UUID(int=97)),
        "family_name": "Example <Family>",
        "family_duid": 1234567,
        "active": True,
        "submitted_at": MOMENT,
        "first_submitted_at": MOMENT.replace(day=1),
        "family_version": 2,
        "annual": MoneyAmount(123450),
        "installment": MoneyAmount(10288),
        "frequency": "monthly",
        "frequency_label": FREQUENCY_LABELS["monthly"],
        "shares": [
            {"label": "Online giving", "text": ""},
            {"label": "Another way", "text": "Stock gift"},
        ],
        "source_pledge": MoneyAmount(120000),
        "source_contributions": MoneyAmount(10000),
    } | changes


def result(rows, *, proven=True):
    """A shaped complete projection with its whole-result summary."""
    return {
        "metadata": {
            "id": str(UUID(int=96)),
            "name": "Sample campaign",
            "timezone": "America/Chicago",
            "source_id": str(UUID(int=95)),
            "source_generation": 12,
            "source_as_of": MOMENT,
            "family_names": FAMILY_NAMES_FULL,
            "giving_observed_at": (
                MOMENT.replace(day=10).isoformat() if proven else None
            ),
            "observed_at": MOMENT.isoformat(),
            "comparison_start": "2025-07-01",
            "comparison_end": "2026-06-30",
            "giving_through": date(2026, 6, 30) if proven else None,
        },
        "summary": {
            "families": len(rows),
            "annual_total": MoneyAmount(sum(item["annual"].cents for item in rows)),
            "frequencies": [(label, 1) for label in FREQUENCY_LABELS.values()],
            "shares": [("Online giving", 1), ("Another way", 1)],
            "no_share": 0,
        },
        "total": len(rows),
        "rows": rows,
    }


def document(rows, *, proven=True, timezone="America/New_York"):
    """Build the document the worker renders, in the requester's timezone."""
    return financial_document(
        result(rows, proven=proven),
        PARAMETERS,
        parish_name="Sample Parish",
        requested_at=MOMENT,
        timezone=timezone,
    )


def test_rows_word_money_status_shares_and_local_instants():
    """Every cell says what the page's cell says, in the export timezone."""
    unproven = row(
        active=None,
        annual=MoneyAmount(0),
        installment=MoneyAmount(None),
        frequency="",
        frequency_label=FREQUENCY_LABELS["none"],
        shares=[],
        source_pledge=MoneyAmount(None),
        source_contributions=MoneyAmount(None),
    )
    built = document([row(), unproven, row(active=False)])
    assert built.item_count == 3 and len(built.headings) == len(HEADINGS)
    assert built.rows[0] == (
        "Example <Family>",
        "1234567",
        "Active",
        MoneyAmount(123450),
        "Monthly",
        MoneyAmount(10288),
        "Online giving; Another way: Stock gift",
        MoneyAmount(120000),
        MoneyAmount(10000),
        datetime(2026, 9, 19, 11, 4, tzinfo=NEW_YORK),
        datetime(2026, 9, 1, 11, 4, tzinfo=NEW_YORK),
        "2",
        str(UUID(int=97)),
    )
    # A zero pledge has no frequency or installment; unproven money is typed
    # unavailable, which every renderer words, never a zero.
    assert built.rows[1][2:9] == (
        "Status unavailable",
        MoneyAmount(0),
        "No frequency",
        "",
        "None chosen",
        MoneyAmount(None),
        MoneyAmount(None),
    )
    assert built.rows[2][2] == "Inactive"
    metadata = dict(built.metadata)
    assert metadata["Report"] == "Financial stewardship detail"
    # Typed display-zone values; each renderer formats them (see #221).
    assert metadata["Source as of"] == datetime(2026, 9, 19, 11, 4, tzinfo=NEW_YORK)
    assert metadata["Family names"] == FAMILY_NAMES_FULL
    # The money's own read time is stated apart from the source promotion time.
    assert metadata["ParishSoft giving read as of"] == datetime(
        2026, 9, 10, 11, 4, tzinfo=NEW_YORK
    )
    assert metadata["Captured at"] == metadata["Source as of"]
    assert metadata["Requested at"] == metadata["Source as of"]
    assert metadata["Display timezone"] == "America/New_York"
    assert metadata["Campaign date-filter timezone"] == "America/Chicago"
    assert metadata["ParishSoft comparison period"] == Span(
        date(2025, 7, 1), date(2026, 6, 30)
    )
    assert csv_text(metadata["ParishSoft comparison period"]) == (
        "2025-07-01 through 2026-06-30"
    )
    assert metadata["ParishSoft contributions through"] == date(2026, 6, 30)
    assert metadata["Matching Families"] == "3"
    assert metadata["Total annual pledges"] == MoneyAmount(246900)
    # Counts are wrapped values, never keys, so a long label cannot break a PDF.
    assert "Monthly: 1" in metadata["Pledges by frequency"].split("\n")
    assert metadata["Pledges by share method"] == "Online giving: 1\nAnother way: 1"
    assert metadata["No share method chosen"] == "0"
    assert '"sort": "name"' in metadata["Filters and sort"]


def test_an_unproven_capture_says_unavailable_never_zero():
    """Without a proven giving read the file explains, and no total reads as zero."""
    built = document([row(source_pledge=MoneyAmount(None))], proven=False)
    metadata = dict(built.metadata)
    assert metadata["ParishSoft contributions through"] == UNPROVEN
    assert metadata["ParishSoft giving read as of"] == "Unavailable"
    assert built.rows[0][7] == MoneyAmount(None)


def test_share_wording_beyond_a_spreadsheet_cell_continues_in_later_rows():
    """A hundred options with long Other text never truncate silently in XLSX."""
    from openpyxl import load_workbook

    # Other text the spreadsheet cannot store as is: XML carries neither a
    # noncharacter nor a bare backslash, so the writer spells each as an escape
    # up to six characters long, and the cells are packed by that length.
    shares = [
        {"label": f"Option {index}", "text": f"{index}:" + "￾\\" * 995}
        for index in range(17)
    ]
    built = document([row(shares=shares), row(family_duid=2)])
    # Each entry stores as about eight thousand characters: four per cell.
    assert built.item_count == 2 and len(built.rows) == 6
    assert [cell[1] for cell in built.rows] == ["1234567"] * 5 + ["2"]
    # A continuation row carries the Family, the wording and the reference only.
    assert built.rows[1][2:6] == ("", "", "", "") and built.rows[1][7:12] == ("",) * 5
    assert built.rows[1][6].startswith("(continued) Option ")
    assert built.rows[1][12] == str(UUID(int=97))
    expected = visible_text("; ".join(f"{s['label']}: {s['text']}" for s in shares))
    # Every character survives a workbook round trip, in order, in the
    # writer's own spelling, and no stored cell exceeds the maximum.
    output = io.BytesIO()
    render_information(built, output, format="xlsx")
    sheet = load_workbook(io.BytesIO(output.getvalue()))["Financial detail"]
    cells = [str(sheet.cell(index, 7).value) for index in range(2, 7)]
    assert all(len(cell) <= 32_767 for cell in cells)
    assert "; ".join(cell.removeprefix("(continued) ") for cell in cells) == expected
    # The CSV lays out the same six rows, with the continuation cells in the
    # share column and nothing in the status or money columns.
    output = io.BytesIO()
    render_information(built, output, format="csv")
    records = list(csv.reader(io.StringIO(output.getvalue().decode())))
    data = records[2:]
    assert len(data) == 6 and [item[1] for item in data] == [c[1] for c in built.rows]
    assert data[1][6].startswith("(continued) ") and data[1][2] == data[1][3] == ""
    assert data[1][12] == str(UUID(int=97))
    output = io.BytesIO()
    render_information(built, output, format="pdf")
    assert output.getvalue().startswith(b"%PDF")
    # The PDF joins each continuation to its Family's one card.
    from parishkit.stewardship.reports.information_rendering import (
        information_records,
    )

    _, first, second = information_records(built)
    assert first.title == "Example <Family> (DUID 1234567)"
    shares = [value for label, value in first.fields if label == "Share methods"]
    assert len(shares) == 5 and shares[1].startswith("(continued) ")
    assert second.title == "Example <Family> (DUID 2)"
    # A long share label is a wrapped metadata value, so the PDF still renders.
    long = row(shares=[{"label": "L" * 150, "text": ""}])
    shaped = result([long])
    shaped["summary"]["shares"] = [("L" * 150, 1)]
    output = io.BytesIO()
    render_information(
        financial_document(
            shaped,
            PARAMETERS,
            parish_name="Sample Parish",
            requested_at=MOMENT,
            timezone="UTC",
        ),
        output,
        format="pdf",
    )
    assert output.getvalue().startswith(b"%PDF")


def test_an_incomplete_capture_is_refused():
    """A document is the whole result or nothing; a page can never masquerade."""
    partial = result([row()])
    partial["total"] = 2
    with pytest.raises(ValueError):
        financial_document(
            partial,
            PARAMETERS,
            parish_name="Sample Parish",
            requested_at=MOMENT,
            timezone="UTC",
        )
    with pytest.raises(ValueError):
        document([row(submitted_at=MOMENT.replace(tzinfo=None))])


@pytest.mark.parametrize("format", ["csv", "xlsx", "pdf"])
def test_every_format_renders_from_one_document(format):
    """The shared renderer carries headings, rows and metadata into each file."""
    output = io.BytesIO()
    render_information(document([row()]), output, format=format)
    body = output.getvalue()
    assert body.startswith({"csv": b"Family,", "xlsx": b"PK", "pdf": b"%PDF"}[format])
    if format == "csv":
        text = body.decode()
        # CSV money is the canonical amount (#388 L5).
        assert "Example <Family>" in text and ",1234.50," in text
        assert "Financial stewardship detail" in text and ",1200.00," in text
        assert "$" not in text


def test_comparison_headings_match_the_page():
    """CSV and XLSX head the ParishSoft columns with the page's labels (#404)
    and its comparison years (#932)."""
    from pathlib import Path

    from openpyxl import load_workbook

    import parishkit.stewardship.accounts as accounts

    labels = ["ParishSoft pledged", "ParishSoft contributed"]
    assert list(HEADINGS[7:9]) == labels
    assert not any(heading.startswith("Source") for heading in HEADINGS)
    # The sample period runs July 2025 to June 2026, as the page words it.
    expected = [f"{label} (2025–2026)" for label in labels]
    assert list(document([row()]).headings[7:9]) == expected
    # Only those two headings change; a one-year period names one year.
    single = headings("2026-01-01", "2026-12-31")
    assert single[7:9] == ("ParishSoft pledged (2026)", "ParishSoft contributed (2026)")
    assert single[:7] + single[9:] == HEADINGS[:7] + HEADINGS[9:]
    assert headings("", "") is HEADINGS
    output = io.BytesIO()
    render_information(document([row()]), output, format="csv")
    header = next(csv.reader(io.StringIO(output.getvalue().decode())))
    assert header[7:9] == expected
    output = io.BytesIO()
    render_information(document([row()]), output, format="xlsx")
    sheet = load_workbook(io.BytesIO(output.getvalue()))["Financial detail"]
    assert [sheet["H1"].value, sheet["I1"].value] == expected
    template = (
        Path(accounts.__file__).parent / "templates/stewardship/financial-report.html"
    ).read_text(encoding="utf-8")
    for heading in labels:
        assert f'{{% translate "{heading}"' in template
    # The stopgap About sentence mapping the old export names is gone.
    assert "Source pledged" not in template
    # The unavailable-giving note says what the page's notice says.
    shared = (
        "the latest giving data read from ParishSoft is not confirmed complete "
        "for this campaign's comparison period. Unavailable does not mean zero."
    )
    assert shared in template and shared in UNPROVEN


def money_document():
    """Ordinary, zero, one-cent, negative, unavailable and oversized amounts."""
    return document(
        [
            row(),
            row(
                family_duid=2,
                annual=MoneyAmount(0),
                installment=MoneyAmount(None),
                source_pledge=MoneyAmount(-5000),
                source_contributions=MoneyAmount(None),
            ),
            # Seventeen significant digits: more than Excel can hold exactly.
            row(
                family_duid=3,
                annual=MoneyAmount(1),
                installment=MoneyAmount(1),
                source_pledge=MoneyAmount(12345678901234567),
            ),
        ]
    )


def test_xlsx_money_cells_are_exact_summable_numbers():
    """Known money is a dollar-formatted number; absence is never a zero."""
    from decimal import Decimal

    from openpyxl import load_workbook

    output = io.BytesIO()
    render_information(money_document(), output, format="xlsx")
    book = load_workbook(io.BytesIO(output.getvalue()))
    sheet = book["Financial detail"]
    # Columns D, F, H and I: annual, installment, ParishSoft pledged, contributed.
    for reference, expected in {
        "D2": "1234.50",
        "F2": "102.88",
        "H2": "1200.00",
        "I2": "100.00",
        "D3": "0.00",
        "H3": "-50.00",
        "D4": "0.01",
        "F4": "0.01",
    }.items():
        cell = sheet[reference]
        assert cell.data_type == "n", reference
        assert cell.number_format == '"$"#,##0.00', reference
        assert Decimal(str(round(float(cell.value), 2))) == Decimal(expected), reference
    # Unavailable stays the word; a missing installment stays blank, never 0;
    # an amount beyond Excel's precision stays exact text.
    assert sheet["I3"].value == "Unavailable" and sheet["I3"].data_type == "s"
    assert sheet["F3"].value is None
    assert sheet["H4"].value == "$123,456,789,012,345.67"
    assert sheet["H4"].data_type == "s"
    metadata = book["Report information"]
    total = next(
        cells[1]
        for cells in metadata.iter_rows()
        if cells[0].value == "Total annual pledges"
    )
    assert total.data_type == "n" and total.number_format == '"$"#,##0.00'
    assert Decimal(str(round(float(total.value), 2))) == Decimal("1234.51")
    book.close()


def test_csv_money_is_the_canonical_amount_and_pdf_keeps_the_page_text():
    """CSV writes plain signed decimals; PDF still writes the page's text."""
    from parishkit.stewardship.reports.information_rendering import (
        information_records,
    )

    from .test_information_rendering import card_text

    built = money_document()
    output = io.BytesIO()
    render_information(built, output, format="csv")
    records = list(csv.reader(io.StringIO(output.getvalue().decode())))
    first, second, third = records[2:]
    assert first[3:9] == [
        "1234.50",
        "Monthly",
        "102.88",
        "Online giving; Another way: Stock gift",
        "1200.00",
        "100.00",
    ]
    # A negative amount is a plain signed decimal, with no apostrophe; an
    # unavailable amount stays the word and an absent one blank (#388 L5).
    assert (second[3], second[5], second[7], second[8]) == (
        "0.00",
        "",
        "-50.00",
        "Unavailable",
    )
    # Beyond Excel's digits too: the exact amount, never rounded.
    assert third[7] == "123456789012345.67"
    # The report-total metadata trailer is a canonical amount too.
    assert records[0].index("Total annual pledges") == first.index("1234.51")
    lines = [
        f"{label}: {text}" for label, text in card_text(information_records(built))
    ]
    assert "Annual pledge: $1,234.50" in lines
    assert "ParishSoft pledged (2025–2026): -$50.00" in lines
    assert "Total annual pledges: $1,234.51" in lines


def test_xlsx_money_is_accurate_to_the_cent_despite_doubles():
    """A spreadsheet number is a double: exact cents survive, not exact digits."""
    from decimal import Decimal

    from openpyxl import load_workbook

    amounts = {"D2": 9757, "F2": 82381, "H2": 1, "I2": -9757}
    built = document(
        [
            row(
                annual=MoneyAmount(amounts["D2"]),
                installment=MoneyAmount(amounts["F2"]),
                source_pledge=MoneyAmount(amounts["H2"]),
                source_contributions=MoneyAmount(amounts["I2"]),
            )
        ]
    )
    output = io.BytesIO()
    render_information(built, output, format="xlsx")
    book = load_workbook(io.BytesIO(output.getvalue()))
    sheet = book["Financial detail"]
    for reference, cents in amounts.items():
        cell = sheet[reference]
        exact = MoneyAmount(cents).decimal
        assert cell.data_type == "n", reference
        # Read back, the stored double rounds to the exact cent.
        assert Decimal(str(round(float(cell.value), 2))) == exact, reference
    book.close()
