"""Directory export files: plain one-row-per-Family columns without startup."""

import csv
import io
from datetime import UTC, datetime

import pytest
from openpyxl import load_workbook

from parishkit.stewardship.reports.directory_documents import (
    CODE_HEADINGS,
    POSTAL_HEADINGS,
    directory_document,
    head_names,
)
from parishkit.stewardship.reports.directory_rendering import (
    render_directory,
    table_lines,
)

MOMENT = datetime(2026, 10, 2, 13, tzinfo=UTC)
ADDRESS = dict(
    primaryAddress1="1 Sample St",
    primaryAddress2=None,
    primaryAddress3="Apt 4",
    primaryCity="Town",
    primaryState="KY",
    primaryPostalCode="40223",
    primaryZipPlus="1234",
)


def item(**values):
    """One captured Family row with every field the renderers read."""
    return (
        dict(
            family_name="=Sample Family",
            family_duid=12345,
            code="ABCDEFGH",
            heads=[
                {"name": "Aaron Williams", "first": "Aaron", "last": "Williams"},
                {"name": "Isabelle Williams", "first": "Isabelle", "last": "Williams"},
            ],
            phones=[dict(owner="Family", kind="home", value="202-555-0123")],
            address=ADDRESS,
            mailable=True,
        )
        | values
    )


def document(rows, *, postal=False, reach="any"):
    """Build a document the way the export worker does."""
    payload = dict(
        metadata=dict(
            name="Annual campaign",
            id="campaign",
            source_id="source",
            source_generation=1234,
            source_as_of=MOMENT.isoformat(),
        ),
        total=len(rows),
        rows=rows,
    )
    return directory_document(
        payload,
        {
            "filters": {
                "search": "",
                "reason": "any",
                "phone": "yes",
                "response": "any",
                "sort": "name",
                "reach": reach,
            },
            "postal": postal,
            "exact": False,
        },
        parish_name="Sample Parish",
        captured_at=MOMENT,
        requested_at=MOMENT,
        timezone="America/Detroit",
    )


def csv_rows(report):
    """Render CSV and parse it back."""
    output = io.BytesIO()
    render_directory(report, output, format="csv")
    return list(csv.reader(io.StringIO(output.getvalue().decode())))


@pytest.mark.parametrize(
    "heads,expected",
    [
        ([], ""),
        ([{"name": "Ann Lee", "first": "Ann", "last": "Lee"}], "Ann Lee"),
        (
            [
                {"name": "Aaron Williams", "first": "Aaron", "last": "Williams"},
                {"name": "Isabelle Smith", "first": "Isabelle", "last": "Smith"},
            ],
            "Aaron Williams and Isabelle Smith",
        ),
        (
            [
                {"name": "A Ng", "first": "A", "last": "Ng"},
                {"name": "B Ng", "first": "B", "last": "Ng"},
                {"name": "C Ng", "first": "C", "last": "Ng"},
            ],
            "A, B and C Ng",
        ),
        (
            [{"name": "Old Capture"}, {"name": "Second Head"}],
            "Old Capture and Second Head",
        ),
    ],
)
def test_head_names_join_naturally(heads, expected):
    """Shared surnames are said once; otherwise each full name is kept."""
    assert head_names(heads) == expected


def test_code_directory_csv_has_exactly_four_plain_columns():
    """No record column, no metadata row: a header and one row per Family."""
    rows = csv_rows(document([item(family_duid=12345 + index) for index in range(52)]))
    assert rows[0] == list(CODE_HEADINGS)
    assert len(rows) == 53
    assert rows[1] == [
        "'=Sample Family",
        "Aaron and Isabelle Williams",
        "12345",
        "ABCDEFGH",
    ]


def test_unreachable_directory_adds_phone_numbers():
    """The "neither email nor mail" list adds phones for follow-up calls."""
    rows = csv_rows(document([item()], reach="neither"))
    assert rows[0] == [*CODE_HEADINGS, "Phone numbers"]
    assert rows[1][-1] == "Family (home): +1 (202) 555-0123"


def test_postal_csv_is_a_mail_merge_without_unmailable_families():
    """Addressee, compacted address lines and ZIP+4; unmailable Families counted."""
    report = document(
        [
            item(),
            item(
                family_duid=2,
                family_name="No Street",
                address={"primaryCity": "X"},
                mailable=False,
            ),
            item(family_duid=3, heads=[], address=ADDRESS | {"primaryZipPlus": ""}),
        ],
        postal=True,
    )
    assert report.excluded == 1 and report.item_count == 3
    rows = csv_rows(report)
    assert rows[0] == list(POSTAL_HEADINGS)
    assert rows[1] == [
        "12345",
        "'=Sample Family",
        "Aaron and Isabelle Williams",
        "Aaron and Isabelle Williams",
        "1 Sample St",
        "Apt 4",
        "",
        "Town",
        "KY",
        "40223-1234",
        "ABCDEFGH",
    ]
    # No heads: the Family name addresses the envelope.
    assert (
        rows[2][2] == "'=Sample Family" and rows[2][3] == "" and rows[2][9] == "40223"
    )
    assert len(rows) == 3
    details = dict(report.metadata)
    assert details["Not in this file: no usable mailing address"] == "1"
    assert details["Filters applied"] == "Phone available: Yes"


def test_xlsx_and_pdf_carry_the_same_columns_and_details():
    """XLSX has the plain sheet; PDF fits the table with details in the header."""
    report = document([item()])
    output = io.BytesIO()
    render_directory(report, output, format="xlsx")
    book = load_workbook(output)
    sheet = book["Families"]
    assert tuple(cell.value for cell in sheet[1]) == CODE_HEADINGS
    assert sheet["A2"].value == "=Sample Family" and sheet["A2"].data_type == "s"
    assert dict(
        (row[0].value, row[1].value) for row in book["Report information"].iter_rows()
    )["Privacy"].startswith("Sensitive: Family codes.")
    book.close()
    line = next(table_lines(report))[0]
    assert "Aaron and Isabelle Williams" in line and "ABCDEFGH" in line
    for postal in (False, True):
        output = io.BytesIO()
        assert render_directory(document([item()], postal=postal), output, format="pdf")
        assert output.getvalue().startswith(b"%PDF")


@pytest.mark.parametrize("format", ["csv", "xlsx", "pdf"])
@pytest.mark.parametrize("postal", [False, True])
def test_empty_directory_exports_still_render(format, postal):
    """An empty selection is a labeled, header-only file, not an error."""
    report = document([], postal=postal)
    output = io.BytesIO()
    render_directory(report, output, format=format)
    assert output.getvalue()
    assert dict(report.metadata)["Families in this file"] == "0"


def test_captures_without_the_mailable_flag_use_the_same_rule():
    """Older captures lack the SQL flag; the Python rule decides the same way."""
    old = [
        {key: value for key, value in item().items() if key != "mailable"},
        {
            key: value
            for key, value in item(address={"primaryAddress1": "1 St"}).items()
            if key != "mailable"
        },
    ]
    report = document(old, postal=True)
    assert report.excluded == 1 and len(report.rows) == 1
