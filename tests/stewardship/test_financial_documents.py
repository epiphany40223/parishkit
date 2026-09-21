"""Detached financial export documents render every format from one capture."""

import io
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from parishkit.stewardship.reports.financial import FREQUENCY_LABELS, FinancialQuery
from parishkit.stewardship.reports.financial_documents import (
    HEADINGS,
    UNPROVEN,
    financial_document,
)
from parishkit.stewardship.reports.information_rendering import render_information
from parishkit.stewardship.reports.money import MoneyAmount

MOMENT = datetime(2026, 9, 19, 15, 4, tzinfo=UTC)
PARAMETERS = {"filters": FinancialQuery().form_values(), "proof": None}


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
    assert built.item_count == 3 and built.headings is HEADINGS
    assert built.rows[0] == (
        "Example <Family>",
        "1234567",
        "Active",
        "$1,234.50",
        "Monthly",
        "$102.88",
        "Online giving; Another way: Stock gift",
        "$1,200.00",
        "$100.00",
        "2026-09-19T11:04:00-04:00",
        "2026-09-01T11:04:00-04:00",
        "2",
        str(UUID(int=97)),
    )
    # A zero pledge has no frequency or installment; unproven money is the word.
    assert built.rows[1][2:9] == (
        "Status unavailable",
        "$0.00",
        "No frequency",
        "",
        "None chosen",
        "Unavailable",
        "Unavailable",
    )
    assert built.rows[2][2] == "Inactive"
    metadata = dict(built.metadata)
    assert metadata["Report"] == "Financial stewardship detail"
    assert metadata["Source as of"] == "2026-09-19T11:04:00-04:00"
    assert metadata["Captured at"] == metadata["Source as of"]
    assert metadata["Requested at"] == metadata["Source as of"]
    assert metadata["Display timezone"] == "America/New_York"
    assert metadata["Campaign date-filter timezone"] == "America/Chicago"
    assert metadata["Source comparison period"] == "2025-07-01 through 2026-06-30"
    assert metadata["Source contributions through"] == "2026-06-30"
    assert metadata["Matching Families"] == "3"
    assert metadata["Total annual pledges"] == "$2,469.00"
    assert metadata["Frequency: Monthly"] == "1"
    assert metadata["Share method: Another way"] == "1"
    assert metadata["No share method chosen"] == "0"
    assert '"sort": "name"' in metadata["Filters and sort"]


def test_an_unproven_capture_says_unavailable_never_zero():
    """Without a proven giving read the file explains, and no total reads as zero."""
    built = document([row(source_pledge=MoneyAmount(None))], proven=False)
    assert dict(built.metadata)["Source contributions through"] == UNPROVEN
    assert built.rows[0][7] == "Unavailable"


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
        assert "Example <Family>" in text and "$1,234.50" in text
        assert "Financial stewardship detail" in text and "$1,200.00" in text
