"""Roster changes to enter: eligibility and the download (#528, step 4)."""

import csv
import io
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook

from parishkit.stewardship.reports import pdf_design
from parishkit.stewardship.reports.ministry_roster_views import (
    HEADINGS,
    PDF_WEIGHTS,
    SORTING,
    roster_csv,
    roster_pdf,
    roster_xlsx,
)
from parishkit.stewardship.workflows.roster import eligible

WHEN = datetime(2054, 10, 5, 14, 30, tzinfo=UTC)


def roster_row(**values):
    """One listed roster change, as ``roster_rows`` shapes it, with overrides."""
    return {
        "id": "1",
        "member_name": "Sample, Pat",
        "member_duid": 3,
        "family_name": "Sample, Pat and Lee",
        "family_duid": 1,
        "ministry_name": "Food pantry",
        "ministry_duid": 9,
        "action": "join",
        "resolved_at": WHEN,
        "resolved_by": "admin@example.org",
        "roster_entered": True,
        "roster_by": "staff@example.org",
        "roster_at": WHEN,
    } | values


def test_only_person_resolved_roster_changes_take_the_tick():
    """Resolved joins and leaves with no resolution source; nothing else."""
    base = {"state": "resolved", "outcome": "joined", "source_resolved": False}
    assert eligible(base)
    assert eligible(base | {"outcome": "leave_confirmed"})
    assert not eligible(base | {"source_resolved": True})
    assert not eligible(base | {"outcome": "declined"})
    assert not eligible(base | {"state": "superseded"})


def test_download_times_are_in_the_chosen_zone_and_formulas_are_neutral():
    """Instants carry the zone's offset; a formula-like name stays text."""
    row = roster_row(member_name="=HYPERLINK(1)")
    text = roster_csv([row], ZoneInfo("America/Chicago")).decode()
    table = list(csv.reader(io.StringIO(text)))
    assert tuple(table[0]) == HEADINGS
    line = dict(zip(HEADINGS, table[1], strict=True))
    assert not line["Member"].startswith("=")
    assert line["Resolved"] == "2054-10-05T09:30:00-05:00"
    assert line["Resolved by"] == "admin@example.org"
    assert line["Entered at"] == "2054-10-05T09:30:00-05:00"
    assert line["Family"] == "Sample, Pat and Lee" and line["Family DUID"] == "1"
    assert line["Entered in ParishSoft"] == "Yes"


def test_download_words_a_new_member_and_an_unentered_change_as_the_page():
    """A Member added on the form has "New Member" for a DUID; a change not
    yet entered says Not yet, with no one and no time."""
    row = roster_row(
        member_duid=None, roster_entered=False, roster_by="", roster_at=None
    )
    text = roster_csv([row], ZoneInfo("UTC")).decode()
    line = dict(zip(HEADINGS, list(csv.reader(io.StringIO(text)))[1], strict=True))
    assert line["Member DUID"] == "New Member"
    assert line["Entered in ParishSoft"] == "Not yet"
    assert line["Entered by"] == "" and line["Entered at"] == ""


def test_csv_xlsx_and_pdf_carry_the_same_columns_and_rows():
    """One column set in every format (#966): the XLSX cells are the CSV's,
    and the PDF table has the same headings and values, only its times worded
    in the parish's compact date format."""
    zone = ZoneInfo("America/Chicago")
    rows = [roster_row(), roster_row(id="2", action="leave", member_duid=None)]
    table = list(csv.reader(io.StringIO(roster_csv(rows, zone).decode())))
    book = load_workbook(io.BytesIO(roster_xlsx(rows, zone)))
    sheet = [
        ["" if value is None else value for value in values]
        for values in book["Roster changes"].iter_rows(values_only=True)
    ]
    assert sheet == table
    drawn = {}
    original = pdf_design.table_pages

    def capture(pdf_table, pdf_rows, frame):
        """Keep the table and rows the PDF lays out, then lay them out."""
        pdf_rows = list(pdf_rows)
        drawn.update(table=pdf_table, rows=pdf_rows, frame=frame)
        return original(pdf_table, pdf_rows, frame)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pdf_design, "table_pages", capture)
        body = roster_pdf(rows, zone, parish="P", campaign="C", as_of=WHEN)
    assert body.startswith(b"%PDF-")
    assert drawn["table"].headings == HEADINGS == tuple(table[0])
    times = {HEADINGS.index("Resolved"), HEADINGS.index("Entered at")}

    def untimed(values):
        """The row's cells other than its times."""
        return [cell for index, cell in enumerate(values) if index not in times]

    assert [untimed(values) for values in drawn["rows"]] == [
        untimed(values) for values in table[1:]
    ]
    assert drawn["rows"][0][HEADINGS.index("Resolved")] == "Oct 5, 2054 9:30 AM"
    assert drawn["frame"].details == ("2 changes in this file.",)
    assert set(PDF_WEIGHTS) == set(HEADINGS)
    # A whole DUID stays on one line; a compact time keeps its date whole.
    for heading, text, lines in (
        ("Member DUID", "1234567", ["1234567"]),
        ("Family DUID", "1234567", ["1234567"]),
        ("Ministry DUID", "1234567", ["1234567"]),
        ("Resolved", "Sep 30, 2026 12:04 PM", ["Sep 30, 2026", "12:04 PM"]),
        ("Entered at", "Sep 30, 2026 12:04 PM", ["Sep 30, 2026", "12:04 PM"]),
    ):
        values = [text if name == heading else "" for name in HEADINGS]
        cell = drawn["table"].cells(values)[HEADINGS.index(heading)]
        assert [line.strip() for line in cell] == lines, heading


def test_every_named_column_sorts_both_ways():
    """Member, Family and Ministry, their DUIDs, the request and the
    resolution time sort in place; a New Member follows the numbered DUIDs."""
    rows = [
        roster_row(id="a", member_duid=7, family_duid=20, ministry_duid=5),
        roster_row(id="b", member_duid=None, family_duid=10, ministry_duid=9),
        roster_row(id="c", member_duid=3, family_duid=30, ministry_duid=1),
    ]
    order = {
        "member_duid": ["c", "a", "b"],
        "duid": ["b", "a", "c"],
        "ministry_duid": ["c", "a", "b"],
    }
    for key, expected in order.items():
        assert [row["id"] for row in SORTING.sort_rows(rows, key)] == expected, key
        assert [row["id"] for row in SORTING.sort_rows(rows, f"-{key}")] == list(
            reversed(expected)
        ), key
