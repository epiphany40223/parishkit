"""Limitations report shaping and exports (#247 follow-up, #970).

The report was "Talents and limitations"; no campaign offers talents now, so
it lists only the limitations, and older answers' stored talents are left
out of the page and its downloads.
"""

import csv
import io
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook

from parishkit.stewardship.reports import pdf_design
from parishkit.stewardship.reports.limitations import (
    FAMILY_CAPTION,
    MEMBER_CAPTION,
    PDF_WEIGHTS,
    LimitationsQuery,
    limitations_csv,
    limitations_pdf,
    limitations_xlsx,
    shape_result,
)
from parishkit.stewardship.responses.service import DEFAULT_TALENTS

PAINTER = DEFAULT_TALENTS[0].id
WHEN = "2026-10-05T14:00:00+00:00"
MEMBER_HEADINGS = [
    "Member",
    "Family",
    "Family DUID",
    "Cannot participate in ministries",
    "Latest response",
]
FAMILY_HEADINGS = ["Family", "Family DUID", "Latest response"]


def result(talents, counts, *, cannot_serve=True):
    """A raw SQL projection with one Member and one Family."""
    return {
        "members": [
            {
                "family_name": "Example",
                "family_duid": 10,
                "member_name": "Alex Example",
                "proposed": False,
                "cannot_serve": cannot_serve,
                "talents": talents,
                "submitted_at": WHEN,
            }
        ],
        "families": [
            {"family_name": "Example", "family_duid": 10, "submitted_at": WHEN}
        ],
        "summary": {
            "members": 1,
            "cannot_serve": int(cannot_serve),
            "cannot_attend": 1,
            "talents": counts,
        },
    }


def test_stored_talents_are_left_out_and_only_limitations_listed():
    """Stored talents never show (#970).

    A Member the SQL lists only for a talent an earlier response chose is left
    out; a Member who cannot participate stays, and no row or summary keeps
    the stored talents.
    """
    raw = result({PAINTER: ""}, {PAINTER: 2}, cannot_serve=False)
    raw["members"].append(
        raw["members"][0] | {"member_name": "Sam Example", "cannot_serve": True}
    )
    raw["summary"] |= {"members": 2, "cannot_serve": 1}
    shaped = shape_result(raw)
    assert [row["member_name"] for row in shaped["members"]] == ["Sam Example"]
    assert "talents" not in shaped["members"][0]
    assert shaped["summary"] == {"cannot_serve": 1, "cannot_attend": 1}


def test_downloads_keep_the_limitations_columns():
    """CSV and XLSX keep the limitations-only columns the report had (#970).

    Each table has its page table's columns in the page's order (#932,
    #966): the Members table leads with the Member, and the Families table
    has no constant Cannot attend Mass column.
    """
    shaped = shape_result(result({PAINTER: ""}, {PAINTER: 1}))
    rows = list(csv.reader(io.StringIO(limitations_csv(shaped, UTC).decode())))
    assert rows[0] == MEMBER_HEADINGS
    assert rows[1] == ["Alex Example", "Example", "10", "Yes", WHEN]
    assert rows[2] == [] and rows[3] == FAMILY_HEADINGS
    assert rows[4] == ["Example", "10", WHEN]
    book = load_workbook(io.BytesIO(limitations_xlsx(shaped, ZoneInfo("UTC"))))
    assert [cell.value for cell in book["Members"][1]] == MEMBER_HEADINGS
    assert [cell.value for cell in book["Families"][1]] == FAMILY_HEADINGS


def test_pdf_prints_both_tables_with_the_csv_columns_and_rows(monkeypatch):
    """The PDF (#966) is the CSV's two tables, Members then Families.

    Each table has the CSV's headings and values, only Latest response
    worded in the parish's date format; the Families table starts on its
    own page, and one frame names the parish, campaign and both counts.
    """
    zone = ZoneInfo("America/Chicago")
    raw = result({}, {})
    raw["members"][0]["member_name"] = "=cmd"
    shaped = shape_result(raw)
    drawn, original = [], pdf_design.table_pages

    def capture(table, rows, frame):
        """Keep each table and its rows, then lay them out."""
        rows = list(rows)
        drawn.append((table, rows, frame))
        return original(table, rows, frame)

    monkeypatch.setattr(pdf_design, "table_pages", capture)
    body = limitations_pdf(
        shaped,
        zone,
        parish="Example Parish",
        campaign="Stewardship 2055",
        as_of=datetime.fromisoformat(WHEN),
    )
    # Two pages: the Families table does not share the Members' page.
    assert body.startswith(b"%PDF-") and b"/Count 2" in body
    csv_rows = list(csv.reader(io.StringIO(limitations_csv(shaped, zone).decode())))
    (members, member_rows, frame), (families, family_rows, _) = drawn
    assert list(members.headings) == csv_rows[0] == MEMBER_HEADINGS
    assert list(families.headings) == csv_rows[3] == FAMILY_HEADINGS
    # The formula-like name prints as typed; the CSV guards it.
    assert member_rows[0][:4] == ("=cmd", "Example", "10", "Yes")
    assert member_rows[0][4] == family_rows[0][2] == "Oct 5, 2026 9:00 AM"
    assert family_rows[0][:2] == ("Example", "10")
    assert frame.eyebrow == "Example Parish · Stewardship 2055"
    # The tables' frame adds two blank lines of room for their captions.
    assert frame.details == (
        "1 Member cannot participate in ministries; 1 Family cannot attend Mass.",
        "",
        "",
    )
    assert set(PDF_WEIGHTS) == {*MEMBER_HEADINGS, *FAMILY_HEADINGS}
    for table in (members, families):
        assert sum(table.widths) == pytest.approx(pdf_design.BODY_WIDTH)


def test_exports_keep_formula_text_literal():
    """A parish-typed name such as =cmd is never a spreadsheet formula."""
    shaped = shape_result(result({}, {}))
    shaped["members"][0]["member_name"] = "=cmd"
    rows = list(csv.reader(io.StringIO(limitations_csv(shaped, UTC).decode())))
    assert rows[1][0] == "'=cmd"
    book = load_workbook(io.BytesIO(limitations_xlsx(shaped, ZoneInfo("UTC"))))
    value = book["Members"]["A2"]
    assert value.data_type == "s" and value.value == "=cmd"


def test_xlsx_escapes_control_characters_in_source_names():
    """A vertical tab pasted into a ParishSoft name cannot break the workbook.

    openpyxl rejects characters XML 1.0 forbids, so a raw write raised and the
    download failed. The shared writer escapes them reversibly instead.
    """
    raw = result({}, {})
    raw["members"][0]["member_name"] = "Alex\x0bExample"
    raw["families"][0]["family_name"] = "Example\x0b"
    shaped = shape_result(raw)
    book = load_workbook(io.BytesIO(limitations_xlsx(shaped, ZoneInfo("UTC"))))
    member = book["Members"]["A2"]
    assert member.data_type == "s" and member.value == "Alex\\u000bExample"
    assert book["Families"]["A2"].value == "Example\\u000b"


def page(raw, paging=None):
    """Render the report page from a shaped result, as the view does."""
    from django.template.loader import render_to_string

    from parishkit.stewardship.reports.limitation_views import tables

    shaped = shape_result(raw)
    query = LimitationsQuery()
    members, families = tables(shaped, query, paging or {}, "/report")
    return render_to_string(
        "stewardship/limitations-report.html",
        shaped
        | {
            "metadata": {"name": "Renewal"},
            "members_table": members,
            "families_table": families,
            "campaign_id": "00000000-0000-4000-8000-000000000000",
            "query": query,
            "query_fields": query.form_values(),
            "export_timezones": ["UTC"],
        },
    )


def test_report_page_is_named_limitations_and_never_shows_talents():
    """The page is "Limitations", with no talent column, count or filter."""
    html = page(result({PAINTER: ""}, {PAINTER: 1}))
    assert "<h1>Limitations</h1>" in html
    assert "talent" not in html.casefold()
    assert '<select id="limitations-filter" name="show">' in html
    assert '<option value="cannot_serve"' in html
    assert '<option value="cannot_attend"' in html


def test_the_show_filter_takes_only_the_limitation_words():
    """The Show filter is closed; the SQL still names it ``talent``."""
    for word in ("any", "cannot_serve", "cannot_attend"):
        query = LimitationsQuery.parse({"search": "Alex", "show": word})
        assert query.form_values() == {"search": "Alex", "show": word}
        assert query.sql_parameters() == {"search": "Alex", "talent": word}
    # A talent's id (the filter's old values) and the old field name are
    # refused, as any other unknown filter is.
    for invalid in ({"show": PAINTER}, {"talent": "any"}, {"show": "talents"}):
        with pytest.raises(ValueError):
            LimitationsQuery.parse(invalid)


def test_members_table_leads_with_the_member_then_family_and_family_duid():
    """Member, Family, Family DUID, the rest, then Latest response (#932);
    the Family DUID has its own cell and sorts in place."""
    raw = result({}, {})
    raw["members"].append(
        raw["members"][0]
        | {"family_name": "Other", "family_duid": 7, "member_name": "B"}
    )
    html = page(raw, {"members_sort": "duid"})
    start = html.index("<h2>Members who cannot participate in ministries</h2>")
    members = html[start : html.index("<h2>Families that cannot attend")]
    columns = ["member", "family", "duid", "cannot_serve", "latest"]
    positions = [members.index(f'data-sort-column="{key}"') for key in columns]
    assert positions == sorted(positions)
    assert 'data-sort-column="talents"' not in members
    assert '<td>Example</td>\n<td class="numeric">10</td>' in members
    assert "DUID 10" not in members
    # Sorted by Family DUID: 7 comes before 10.
    assert members.index(">7</td>") < members.index(">10</td>")


def captured_pdf(monkeypatch, shaped):
    """Render the PDF; return its body, table layouts and drawn captions."""
    drawn, captions, original = [], [], pdf_design.table_pages

    def capture(table, rows, frame):
        """Keep each table and its rows, then lay them out."""
        rows = list(rows)
        drawn.append((table, rows, frame))
        return original(table, rows, frame)

    text = pdf_design.Canvas.text

    def keep_caption(canvas, x, y, value, size, face="regular", *args, **kwargs):
        """Note each caption drawn, then draw the text."""
        if value in (MEMBER_CAPTION, FAMILY_CAPTION):
            captions.append((value, y))
        return text(canvas, x, y, value, size, face, *args, **kwargs)

    monkeypatch.setattr(pdf_design, "table_pages", capture)
    monkeypatch.setattr(pdf_design.Canvas, "text", keep_caption)
    body = limitations_pdf(
        shaped,
        ZoneInfo("America/Chicago"),
        parish="Example Parish",
        campaign="Stewardship 2055",
        as_of=datetime.fromisoformat(WHEN),
    )
    return body, drawn, captions


def test_pdf_names_each_table_with_its_page_heading(monkeypatch):
    """Every PDF page names its table as the page's table heading does."""
    _, drawn, captions = captured_pdf(monkeypatch, shape_result(result({}, {})))
    assert [caption for caption, _ in captions] == [MEMBER_CAPTION, FAMILY_CAPTION]
    # Each caption sits in the room the tables leave below the header rule
    # (two detail lines), above the table's heading row.
    (_, _, body), _ = drawn
    room = body.body_top - 2 * pdf_design.SMALL_PITCH
    assert all(room < y < body.body_top for _, y in captions)


def test_pdf_with_no_families_still_prints_both_tables(monkeypatch):
    """No Family who cannot attend: the Families page says no rows match.

    The Families table still gets its own captioned page, so the file says
    plainly that the list is empty rather than leaving it out.
    """
    raw = result({}, {})
    raw["families"] = []
    raw["summary"]["cannot_attend"] = 0
    body, drawn, captions = captured_pdf(monkeypatch, shape_result(raw))
    assert body.startswith(b"%PDF-") and b"/Count 2" in body
    (_, members, frame), (families, rows, _) = drawn
    assert len(members) == 1 and rows == []
    assert list(families.headings) == FAMILY_HEADINGS
    assert [caption for caption, _ in captions] == [MEMBER_CAPTION, FAMILY_CAPTION]
    assert frame.details[0].endswith("0 Families cannot attend Mass.")
