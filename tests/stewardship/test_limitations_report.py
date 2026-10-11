"""Limitations report shaping and exports (#247 follow-up, #970).

The report was "Talents and limitations"; no campaign offers talents now, so
it lists only the limitations, and older answers' stored talents are left
out of the page and its downloads.
"""

import csv
import io
from datetime import UTC
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook

from parishkit.stewardship.reports.limitations import (
    LimitationsQuery,
    limitations_csv,
    limitations_xlsx,
    shape_result,
)
from parishkit.stewardship.responses.service import DEFAULT_TALENTS

PAINTER = DEFAULT_TALENTS[0].id
WHEN = "2026-10-05T14:00:00+00:00"
MEMBER_HEADINGS = [
    "Family",
    "Family DUID",
    "Member",
    "Cannot participate in ministries",
    "Latest response",
]
FAMILY_HEADINGS = ["Family", "Family DUID", "Cannot attend Mass", "Latest response"]


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
    """CSV and XLSX keep the limitations-only columns the report had (#970)."""
    shaped = shape_result(result({PAINTER: ""}, {PAINTER: 1}))
    rows = list(csv.reader(io.StringIO(limitations_csv(shaped, UTC).decode())))
    assert rows[0] == MEMBER_HEADINGS
    assert rows[1] == ["Example", "10", "Alex Example", "Yes", WHEN]
    assert rows[2] == [] and rows[3] == FAMILY_HEADINGS
    assert rows[4] == ["Example", "10", "Yes", WHEN]
    book = load_workbook(io.BytesIO(limitations_xlsx(shaped, ZoneInfo("UTC"))))
    assert [cell.value for cell in book["Members"][1]] == MEMBER_HEADINGS
    assert [cell.value for cell in book["Families"][1]] == FAMILY_HEADINGS


def test_exports_keep_formula_text_literal():
    """A parish-typed name such as =cmd is never a spreadsheet formula."""
    shaped = shape_result(result({}, {}))
    shaped["members"][0]["member_name"] = "=cmd"
    rows = list(csv.reader(io.StringIO(limitations_csv(shaped, UTC).decode())))
    assert rows[1][2] == "'=cmd"
    book = load_workbook(io.BytesIO(limitations_xlsx(shaped, ZoneInfo("UTC"))))
    value = book["Members"]["C2"]
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
    member = book["Members"]["C2"]
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
