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
        "latest": True,
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
    # The Ministry is named; its DUID is not a column (#1123).
    assert "Ministry" in HEADINGS and "Ministry DUID" not in HEADINGS
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
    assert line["Replaced by a later response"] == ""
    assert line["Entered by"] == "" and line["Entered at"] == ""
    # A Family that answered again after the change was resolved.
    replaced = roster_csv([roster_row(latest=False)], ZoneInfo("UTC")).decode()
    line = dict(zip(HEADINGS, list(csv.reader(io.StringIO(replaced)))[1], strict=True))
    assert line["Replaced by a later response"] == "Yes"


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
        ("Resolved", "Sep 30, 2026 12:04 PM", ["Sep 30, 2026", "12:04 PM"]),
        ("Entered at", "Sep 30, 2026 12:04 PM", ["Sep 30, 2026", "12:04 PM"]),
    ):
        values = [text if name == heading else "" for name in HEADINGS]
        cell = drawn["table"].cells(values)[HEADINGS.index(heading)]
        assert [line.strip() for line in cell] == lines, heading


def test_every_named_column_sorts_both_ways():
    """Member, Family and Ministry, the Member and Family DUIDs, the request
    and the resolution time sort in place; a New Member follows the numbered
    DUIDs. No Ministry DUID is shown, so none sorts (#1123)."""
    rows = [
        roster_row(id="a", member_duid=7, family_duid=20, ministry_duid=5),
        roster_row(id="b", member_duid=None, family_duid=10, ministry_duid=9),
        roster_row(id="c", member_duid=3, family_duid=30, ministry_duid=1),
    ]
    order = {
        "member_duid": ["c", "a", "b"],
        "duid": ["b", "a", "c"],
    }
    assert "ministry_duid" not in SORTING.columns()
    for key, expected in order.items():
        assert [row["id"] for row in SORTING.sort_rows(rows, key)] == expected, key
        assert [row["id"] for row in SORTING.sort_rows(rows, f"-{key}")] == list(
            reversed(expected)
        ), key


def tick_html(row, **context):
    """The roster tick template rendered for one row and context."""
    from django.template.loader import render_to_string

    return render_to_string(
        "stewardship/roster-tick.html", {"row": row, "roster_key": "k"} | context
    ).strip()


def test_queue_status_cell_says_one_short_line():
    """The follow-up queue's Status cell says only Entered or Not yet
    entered, and never draws the form; who and when are on the request
    page."""

    def cell(**values):
        """The tick as the queue's Status cell draws it for Staff."""
        row = roster_row(roster_eligible=True, source_resolved=False) | values
        return tick_html(row, can_tick=True, compact=True, mutable=True)

    assert cell() == "<br>Entered"
    assert cell(roster_entered=False) == "<br>Not yet entered"
    assert "staff@example.org" not in cell()


def test_a_leader_sees_no_tick_at_all():
    """A Ministry leader (no can_tick) gets nothing: no state, who, when or
    form, on the request page or in the queue (#1160)."""
    for entered in (True, False):
        row = roster_row(
            roster_eligible=True, source_resolved=False, roster_entered=entered
        )
        for compact in (True, False):
            assert tick_html(row, can_tick=False, compact=compact, mutable=True) == ""


def test_download_names_a_member_no_longer_in_parishsoft_in_its_own_column():
    """The Member's ParishSoft status is blank while ParishSoft lists them,
    and otherwise #1216's phrase, beside the Member, not in the name (#1217)."""
    zone = ZoneInfo("UTC")
    rows = [
        roster_row(),
        roster_row(id="2", source_mark=("Deceased in ParishSoft", "reason")),
    ]
    table = list(csv.reader(io.StringIO(roster_csv(rows, zone).decode())))
    lines = [dict(zip(HEADINGS, line, strict=True)) for line in table[1:]]
    assert [line["ParishSoft status"] for line in lines] == [
        "",
        "Deceased in ParishSoft",
    ]
    assert lines[1]["Member"] == "Sample, Pat"


def row_cell_html(row, **context):
    """One row's Actions cell on Roster changes to enter, for one context."""
    from django.template.loader import render_to_string

    return render_to_string(
        "stewardship/roster-tick-row.html",
        {"row": row, "table": {"anchor": "roster"}, "tick_fields": ()} | context,
    ).strip()


def test_the_tick_is_an_icon_button_named_for_its_row():
    """The row's tick is a same-size icon button in the Actions cell (#879),
    its accessible name and tooltip naming the row; a row without a tick,
    or a campaign that admits no change, keeps an empty cell."""
    from uuid import uuid4

    from parishkit.stewardship.reports.ministry_roster_views import row_controls

    row = roster_row(id=str(uuid4()), roster_eligible=True, roster_entered=False)
    row_controls([row], "UTC")
    assert row["label"] == "Sample, Pat — Food pantry"
    cell = row_cell_html(row, mutable=True)
    assert cell.startswith('<td class="table-actions"><form method="post"')
    name = "Mark Sample, Pat — Food pantry entered in ParishSoft"
    assert f'aria-label="{name}"' in cell and f'title="{name}"' in cell
    assert 'class="action-icon"' in cell and 'name="entered" value="yes"' in cell
    cell = row_cell_html(row | {"roster_entered": True}, mutable=True)
    name = "Clear Entered in ParishSoft for Sample, Pat — Food pantry"
    assert f'aria-label="{name}"' in cell and 'name="entered" value="no"' in cell
    empty = '<td class="table-actions"></td>'
    assert row_cell_html(row, mutable=False) == empty
    assert row_cell_html(row | {"roster_eligible": False}, mutable=True) == empty


def test_bulk_post_grammar_is_closed():
    """The bulk bar's post takes only the chosen ticks and the page's key:
    each tick as "<request>:<next sequence>", at most a page, none twice."""
    from uuid import uuid4

    from django.http import QueryDict

    from parishkit.stewardship.reports.ministry_roster_views import (
        MAX_BULK,
        bulk_values,
    )

    key, first, second = uuid4(), uuid4(), uuid4()

    def parse(**values):
        """bulk_values over a form of the given fields (lists for many)."""
        form = QueryDict(mutable=True)
        for name, value in values.items():
            form.setlist(name, value if isinstance(value, list) else [value])
        return bulk_values(form)

    assert parse(bulk_key=str(key), request=[f"{first}:1", f"{second}:3"]) == (
        {first: 1, second: 3},
        key,
    )
    for bad in (
        {"request": [f"{first}:1"]},
        {"bulk_key": str(key)},
        {"bulk_key": [str(key), str(key)], "request": [f"{first}:1"]},
        {"bulk_key": str(key), "request": [f"{first}:1", f"{first}:2"]},
        {"bulk_key": str(key), "request": [f"{first}"]},
        {"bulk_key": str(key), "request": [f"{first}:0"]},
        {"bulk_key": str(key), "request": [f"{first}:1"], "show": "all"},
        {
            "bulk_key": str(key),
            "request": [f"{uuid4()}:1" for _ in range(MAX_BULK + 1)],
        },
    ):
        with pytest.raises(ValueError):
            parse(**bad)


def test_a_change_listed_twice_names_each_row_by_its_resolved_day():
    """The same change listed twice (a Family answered again and both
    requests were resolved) would give both rows one name; those rows add
    their Resolved day in the parish's zone, so each icon, box and dialog
    names its own row. A name no other row shares stays short."""
    from datetime import timedelta

    from parishkit.stewardship.reports.ministry_roster_views import row_controls

    earlier = WHEN - timedelta(days=3)
    rows = [
        roster_row(id="1", resolved_at=WHEN),
        roster_row(id="2", resolved_at=earlier, latest=False),
        roster_row(id="3", member_name="Other, Lee"),
    ]
    row_controls(rows, "America/Los_Angeles")
    assert [row["label"] for row in rows] == [
        "Sample, Pat — Food pantry, resolved Oct 5, 2054",
        "Sample, Pat — Food pantry, resolved Oct 2, 2054",
        "Other, Lee — Food pantry",
    ]
    # The day is the parish's: 14:30 UTC on Oct 5 is still Oct 5 in Los
    # Angeles, but a resolution at 02:00 UTC is the day before there.
    late = [roster_row(id=str(n), resolved_at=WHEN.replace(hour=2)) for n in (1, 2)]
    row_controls(late, "America/Los_Angeles")
    assert late[0]["label"].endswith("resolved Oct 4, 2054")
