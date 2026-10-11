"""Census changes worklist wording, status and filters (#528), without a database."""

import csv
import io
import re
from datetime import UTC, datetime
from html import unescape
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook

from parishkit.stewardship.reports import pdf_design
from parishkit.stewardship.reports.census_change_views import SORTING
from parishkit.stewardship.reports.census_changes import (
    HEADINGS,
    PDF_WEIGHTS,
    CensusQuery,
    census_csv,
    census_pdf,
    census_xlsx,
    display,
    select,
    shape,
    status,
)
from parishkit.stewardship.web.dates import UnknownZone

SUBMITTED = datetime(2054, 10, 5, 14, 30, tzinfo=UTC)


def row(**values):
    """One proposal row as the query returns it, with overrides."""
    return {
        "id": "1",
        "entity_kind": "member",
        "entity_key": "41",
        "field": "mobile_phone",
        "baseline_available": True,
        "baseline_value": "555-0100",
        "submitted_value": "555-0199",
        "current_available": True,
        "current_value": "555-0100",
        "admin_value_set": False,
        "admin_value": None,
        "handling": "api",
        "decision": "unreviewed",
        "execution": "pending",
        "submitted_at": SUBMITTED,
        "family_duid": 7001,
        "family_name": "Sample",
        "member_name": "Pat Sample",
    } | values


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        # A terminal execution is the outcome, whatever the decision says.
        ({"execution": "published", "decision": "ignored"}, "published"),
        ({"execution": "resolved_upstream"}, "already"),
        ({"execution": "resolved_external", "handling": "manual"}, "entered"),
        ({"execution": "superseded"}, "superseded"),
        ({"execution": "cancelled"}, "cancelled"),
        # Then an ignored decision, then the execution.
        ({"decision": "ignored", "execution": "conflict"}, "ignored"),
        ({"decision": "approved", "execution": "queued"}, "being_published"),
        ({"execution": "conflict"}, "conflict"),
        ({"decision": "approved", "execution": "failed"}, "to_do"),
        # A pending automatic change waits for an Administrator's decision.
        ({}, "to_review"),
        ({"decision": "approved"}, "to_do"),
        ({"handling": "manual", "field": "suffix"}, "to_do"),
        # A Family address is worked by hand: no verified address read exists.
        ({"entity_kind": "family", "field": "home_address"}, "to_do"),
    ],
)
def test_status_follows_the_spec_mapping(values, expected):
    """The status comes from decision and execution in the spec's order."""
    assert status(row(**values)) == expected


def test_display_words_each_value_shape():
    """Addresses, Members, phones, choices and nothing read as plain text."""
    address = {
        "line1": "1 Main St",
        "line2": "",
        "city": "Springfield",
        "region": "IL",
        "postal_code": "62701",
        "country": "US",
    }
    assert display(address) == "1 Main St, Springfield, IL, 62701, US"
    assert display({"display": "(555) 010-0199", "normalized": "+15550100199"}) == (
        "(555) 010-0199"
    )
    assert display(True) == "Yes" and display(False) == "No"
    assert display(None) == ""
    member = {"first_name": "Lee", "last_name": "Sample", "email": "lee@example.org"}
    assert display(member) == "Lee Sample; Email address: lee@example.org"


def test_shape_names_who_and_hides_unread_family_values():
    """A Family field has no ParishSoft value; a new Member is named."""
    family = shape(
        row(
            entity_kind="family",
            field="email_opt_out",
            handling="manual",
            current_available=True,
            current_value=False,
            submitted_value=True,
        )
    )
    assert family["who"] == "Family" and family["parishsoft_now"] == ""
    assert family["label"] == "Opt out of all parish emails"
    assert family["route_label"] == "By hand"
    new = shape(
        row(
            entity_kind="proposed_member",
            entity_key="p1",
            field="new_member",
            handling="manual",
            current_available=False,
            submitted_value={"first_name": "Ari", "last_name": "Sample"},
        )
    )
    assert new["who"] == "New Member: Ari Sample" and new["kind"] == "new_member"
    edited = shape(row(admin_value_set=True, admin_value="555-0123"))
    assert edited["edited"] == "555-0123" and edited["route_label"] == "Automatic"


def rows():
    """One row of each status the default view weighs."""
    return [
        shape(row(id="review")),
        shape(row(id="todo", handling="manual", field="suffix")),
        shape(row(id="conflict", execution="conflict")),
        shape(row(id="done", execution="published", decision="approved")),
        shape(row(id="old", execution="superseded")),
        shape(row(id="moved", field="moved_household", handling="manual")),
    ]


def ids(result):
    """The kept rows' ids."""
    return [item["id"] for item in result["rows"]]


def test_default_view_shows_open_work_and_to_review_only_to_administrators():
    """Staff see To do and Conflict; an Administrator also sees To review."""
    query = CensusQuery()
    assert ids(select(rows(), query, administrator=False)) == [
        "todo",
        "conflict",
        "moved",
    ]
    assert ids(select(rows(), query, administrator=True)) == [
        "review",
        "todo",
        "conflict",
        "moved",
    ]


def test_filters_narrow_the_rows():
    """History, route, kind and search each narrow the list."""
    every = CensusQuery(status="all")
    assert "old" not in ids(select(rows(), every, administrator=True))
    history = CensusQuery(status="all", history="yes")
    assert "old" in ids(select(rows(), history, administrator=True))
    by_hand = CensusQuery(status="all", route="by_hand")
    assert ids(select(rows(), by_hand, administrator=True)) == ["todo", "moved"]
    moved = CensusQuery(status="all", kind="moved")
    assert ids(select(rows(), moved, administrator=True)) == ["moved"]
    found = CensusQuery(status="all", search="7001")
    assert len(ids(select(rows(), found, administrator=True))) == 5
    assert ids(select(rows(), CensusQuery(search="nobody"), administrator=True)) == []
    # The summary counts history only when it is shown.
    summary = dict(select(rows(), every, administrator=True)["summary"])
    assert summary["To do"] == 2 and "Superseded" not in summary
    summary = dict(select(rows(), history, administrator=True)["summary"])
    assert summary["Superseded"] == 1


def test_dates_are_days_in_the_browser_zone():
    """From and Through are whole local days; a date needs a known zone."""
    query = CensusQuery.parse(
        {"start": "2054-10-05", "end": "2054-10-05", "zone": "America/Chicago"}
    )
    assert ids(select(rows(), query, administrator=True))
    later = CensusQuery.parse({"start": "2054-10-06", "zone": "America/Chicago"})
    assert ids(select(rows(), later, administrator=True)) == []
    with pytest.raises(UnknownZone):
        CensusQuery.parse({"start": "2054-10-05"})
    assert CensusQuery.parse({"zone": "Nowhere/Zone"}).zone == ""


@pytest.mark.parametrize(
    "values",
    [
        {"status": "superseded"},
        {"route": "api"},
        {"kind": "pledge"},
        {"history": "1"},
        {"start": "2054-13-01", "zone": "UTC"},
        {"start": "2054-10-06", "end": "2054-10-05", "zone": "UTC"},
        {"unknown": "x"},
    ],
)
def test_parse_refuses_values_outside_the_closed_choices(values):
    """Only the offered choices are accepted, as single values."""
    with pytest.raises(ValueError):
        CensusQuery.parse(values)


def test_csv_follows_the_page_column_order_in_the_chosen_zone():
    """The download has the page's columns in the page's order (#932):
    Member DUID right after Who, Submitted last, times in the chosen zone."""
    result = select(rows(), CensusQuery(), administrator=False)
    text = census_csv(result, ZoneInfo("America/Chicago")).decode()
    table = list(csv.reader(io.StringIO(text)))
    assert tuple(table[0]) == HEADINGS
    assert HEADINGS[2:4] == ("Who", "Member DUID")
    assert HEADINGS[-1] == "Submitted"
    assert table[1][2] == "Pat Sample" and table[1][-1] == "2054-10-05T09:30:00-05:00"
    assert len(table) == 4


def test_downloads_word_member_duid_as_the_page_does():
    """CSV and XLSX give Member DUID right after Who: the DUID, "New Member"
    for a Member added on the form, and blank for a household change (#932)."""

    proposed = row(
        id="2",
        entity_kind="proposed_member",
        entity_key="p-1",
        field="new_member",
        submitted_value={"first_name": "Lee", "last_name": "Sample"},
        member_name=None,
    )
    family = row(id="3", entity_kind="family", entity_key="7001", field="home_address")
    result = {"rows": [shape(item) for item in (row(), proposed, family)]}
    zone = ZoneInfo("UTC")
    table = list(csv.reader(io.StringIO(census_csv(result, zone).decode())))
    assert [line[3] for line in table] == ["Member DUID", "41", "New Member", ""]
    sheet = load_workbook(io.BytesIO(census_xlsx(result, zone)))["Census changes"]
    assert sheet.max_column == len(HEADINGS)
    column = HEADINGS.index("Member DUID") + 1
    assert [sheet.cell(line, column).value for line in range(1, 4)] == [
        "Member DUID",
        "41",
        "New Member",
    ]
    assert sheet.cell(4, column).value in (None, "")


def test_family_duid_column_sorts_both_ways():
    """Family DUID has its own column, and it sorts both ways (#932)."""
    shaped = [shape(row(id=str(duid), family_duid=duid)) for duid in (7002, 13, 7001)]
    assert SORTING.tokens["duid"] == ("duid", False)
    up = SORTING.sort_rows(shaped, "duid")
    assert [item["family_duid"] for item in up] == [13, 7001, 7002]
    down = SORTING.sort_rows(shaped, "-duid")
    assert [item["family_duid"] for item in down] == [7002, 7001, 13]


def test_member_duid_comes_from_a_member_rows_key():
    """A Member change shows its DUID; New Members and the Family have none."""
    assert shape(row())["member_duid"] == 41
    proposed = row(
        entity_kind="proposed_member",
        entity_key="p-1",
        field="new_member",
        submitted_value={"first_name": "Lee", "last_name": "Sample"},
        member_name=None,
    )
    assert shape(proposed)["member_duid"] is None
    family = row(entity_kind="family", entity_key="7001", field="home_address")
    assert shape(family)["member_duid"] is None
    # A key that is not a plain integer is left blank, never a broken page.
    assert shape(row(entity_key="x41"))["member_duid"] is None


def test_member_duid_column_sorts_with_blank_rows_after_members():
    """Member DUID sorts in place both ways (#932); rows without one follow
    the Members on the first click."""
    shaped = [
        shape(row(id="a", entity_key="900")),
        shape(row(id="b", entity_kind="family", entity_key="7001")),
        shape(row(id="c", entity_key="41")),
    ]
    assert SORTING.tokens["member_duid"] == ("member_duid", False)
    up = SORTING.sort_rows(shaped, "member_duid")
    assert [item["id"] for item in up] == ["c", "a", "b"]
    down = SORTING.sort_rows(shaped, "-member_duid")
    assert [item["id"] for item in down] == ["b", "a", "c"]


def page_headings(html):
    """The rendered page table's column headings, as a reader sees them.

    A sort heading's arrow and its hidden "(sort ascending)" text are not
    part of the heading's name, so they are dropped with every other tag.
    """
    head = re.search(r"<thead>(.*?)</thead>", html, re.DOTALL).group(1)
    cells = re.findall(r"<th\b[^>]*>(.*?)</th>", head, re.DOTALL)
    hidden = re.compile(r'<span class="(?:sort-indicator|visually-hidden)".*?</span>')
    return [
        unescape(re.sub(r"<[^>]*>", "", hidden.sub("", cell))).strip() for cell in cells
    ]


def test_page_shows_member_duid_beside_who():
    """Who is followed by its own Member DUID column; a New Member says so."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.web.tables import paginate

    rows = [
        shape(row()),
        shape(
            row(
                id="2",
                entity_kind="proposed_member",
                entity_key="p-1",
                field="new_member",
                submitted_value={"first_name": "Lee"},
            )
        ),
    ]
    table = paginate(rows, {}, sorting=SORTING)
    html = render_to_string(
        "stewardship/census-changes.html",
        {
            "table": table,
            "query": CensusQuery(),
            "query_fields": {},
            "summary": [],
            "status_choices": [],
            "route_choices": [],
            "kind_choices": [],
            "export_timezones": ["UTC"],
        },
    )
    headings = [
        "Family",
        "Family DUID",
        "Who",
        "Member DUID",
        "What changed",
        "Submitted",
    ]
    positions = [html.index(f">{heading}<") for heading in headings]
    assert positions == sorted(positions)
    # The downloads have exactly the page's columns, in its order (#932).
    assert page_headings(html) == list(HEADINGS)
    assert '<td class="numeric">41</td>' in html
    assert '<td class="numeric">New Member</td>' in html


def test_csv_xlsx_and_pdf_carry_the_same_columns_and_rows():
    """One column set in every format (#966): CSV, XLSX and the PDF table.

    The XLSX cells are the CSV's; the PDF table has the same headings and
    values, only its Submitted time worded in the parish's date format.
    """
    zone = ZoneInfo("America/Chicago")
    # A formula-like name: the CSV guards it with an apostrophe, the XLSX
    # stores it as literal text and the PDF shows it as typed.
    given = rows()
    given[1] = shape(
        row(id="todo", handling="manual", field="suffix", family_name="=1+2")
    )
    result = select(given, CensusQuery(), administrator=False)
    table = list(csv.reader(io.StringIO(census_csv(result, zone).decode())))
    book = load_workbook(io.BytesIO(census_xlsx(result, zone)))
    sheet = [
        ["" if value is None else value for value in values]
        for values in book["Census changes"].iter_rows(values_only=True)
    ]
    assert "'=1+2" in [values[0] for values in table]
    table = [[cell.removeprefix("'") for cell in values] for values in table]
    assert sheet == table
    formula_like = [
        cell
        for values in book["Census changes"].iter_rows()
        for cell in values
        if cell.value == "=1+2"
    ]
    assert formula_like and all(cell.data_type == "s" for cell in formula_like)
    drawn = {}
    original = pdf_design.table_pages

    def capture(pdf_table, pdf_rows, frame):
        """Keep the table and rows the PDF lays out, then lay them out."""
        pdf_rows = list(pdf_rows)
        drawn.update(table=pdf_table, rows=pdf_rows, frame=frame)
        return original(pdf_table, pdf_rows, frame)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pdf_design, "table_pages", capture)
        body = census_pdf(
            result,
            zone,
            parish="Example Parish",
            campaign="Stewardship 2055",
            as_of=SUBMITTED,
        )
    assert body.startswith(b"%PDF-")
    assert drawn["table"].headings == HEADINGS == tuple(table[0])
    # Every cell matches except Submitted, which the PDF words as a date.
    submitted = HEADINGS.index("Submitted")

    def without_submitted(values):
        """The row's cells other than its Submitted time."""
        return [cell for index, cell in enumerate(values) if index != submitted]

    assert [without_submitted(values) for values in drawn["rows"]] == [
        without_submitted(values) for values in table[1:]
    ]
    assert drawn["rows"][0][submitted] == "Oct 5, 2054 9:30 AM"
    assert drawn["frame"].eyebrow == "Example Parish · Stewardship 2055"
    assert drawn["frame"].details == ("3 changes in this file.",)
    # Every heading has a PDF column width, and the columns fill the page.
    assert set(PDF_WEIGHTS) == set(HEADINGS)
    assert sum(drawn["table"].widths) == pytest.approx(pdf_design.BODY_WIDTH)
    # A compact time and the longest status each stay on one line.
    for heading, text in (
        ("Submitted", "Sep 30, 2026 12:04 PM"),
        ("Status", "Being published"),
    ):
        values = [text if name == heading else "" for name in HEADINGS]
        cell = drawn["table"].cells(values)[HEADINGS.index(heading)]
        assert cell == [text], heading
