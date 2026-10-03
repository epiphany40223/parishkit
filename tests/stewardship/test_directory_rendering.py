"""Directory export files: plain one-row-per-Family columns without startup."""

import csv
import io
from datetime import UTC, datetime

import pytest
from openpyxl import load_workbook

from parishkit.stewardship.reports.directory_documents import (
    CODE_HEADINGS,
    POSTAL_HEADINGS,
    UNADDRESSED_DETAIL,
    directory_document,
    head_names,
)
from parishkit.stewardship.reports.directory_rendering import (
    address_blocks,
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
        # A shared surname is said once even when another surname is present.
        (
            [
                {"name": "Aaron Williams", "first": "Aaron", "last": "Williams"},
                {"name": "Isabelle Williams", "first": "Isabelle", "last": "Williams"},
                {"name": "Carol Smith", "first": "Carol", "last": "Smith"},
            ],
            "Aaron and Isabelle Williams and Carol Smith",
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
    """First names group under each shared surname (heads_salutation_name)."""
    assert head_names(heads) == expected


def test_code_directory_csv_has_exactly_three_plain_columns():
    """No record column, no metadata row: a header and one row per Family.

    Family is the surname, then the heads of household; heads of another
    surname are shown in full.
    """
    rows = csv_rows(document([item(family_duid=12345 + index) for index in range(52)]))
    assert rows[0] == list(CODE_HEADINGS)
    assert len(rows) == 53
    assert rows[1] == [
        "'=Sample Family, Aaron Williams and Isabelle Williams",
        "12345",
        "ABCDEFGH",
    ]
    rows = csv_rows(document([item(family_name="Williams")]))
    assert rows[1][0] == "Williams, Aaron and Isabelle"


def test_unreachable_directory_adds_phone_numbers():
    """The "neither email nor mail" list adds phones for follow-up calls."""
    rows = csv_rows(document([item()], reach="neither"))
    assert rows[0] == [*CODE_HEADINGS, "Phone numbers"]
    assert rows[1][-1] == "Family (home): +1 (202) 555-0123"


def test_postal_csv_is_a_mail_merge_of_every_filtered_family():
    """Addressee, compacted address lines and ZIP+4; unmailable rows blanked."""
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
    assert report.unaddressed == 1 and report.item_count == 3
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
    # No usable address: the row stays, its Addressee and address are blank.
    assert rows[2] == [
        "2",
        "No Street",
        "",
        "Aaron and Isabelle Williams",
        *[""] * 6,
        "ABCDEFGH",
    ]
    # No heads: the Family name addresses the envelope.
    assert (
        rows[3][2] == "'=Sample Family" and rows[3][3] == "" and rows[3][9] == "40223"
    )
    assert len(rows) == 4
    details = dict(report.metadata)
    assert details["Families in this file"] == "3"
    assert details[UNADDRESSED_DETAIL] == "1"
    assert details["Filters applied"] == "Phone available: Yes"


# A filtered set mixing Families with and without a usable mailing address,
# in the order the filters list them; "mailable" is the SQL's verdict.
MIXED = (
    (1, True, ADDRESS),
    (2, False, {"primaryAddress1": "2 Street Only", "primaryState": "KY"}),
    (3, True, ADDRESS | {"primaryAddress3": None, "primaryZipPlus": ""}),
    (4, False, {}),
    (5, True, ADDRESS),
)


@pytest.mark.parametrize("format", ["csv", "xlsx"])
def test_mail_merge_holds_exactly_the_filtered_rows(format):
    """Every filtered Family is a row, in order; no address means blank columns.

    The file must match the page (#202): unaddressable Families are not
    dropped, and none of their partial address leaks into the columns.
    """
    report = document(
        [
            item(family_duid=duid, mailable=usable, address=address)
            for duid, usable, address in MIXED
        ],
        postal=True,
    )
    if format == "csv":
        rows = csv_rows(report)
    else:
        output = io.BytesIO()
        render_directory(report, output, format=format)
        book = load_workbook(output)
        rows = [
            ["" if cell is None else str(cell) for cell in row]
            for row in book["Families"].iter_rows(values_only=True)
        ]
        book.close()
    assert rows[0] == list(POSTAL_HEADINGS)
    assert [row[0] for row in rows[1:]] == [str(duid) for duid, _, _ in MIXED]
    for row, (_, usable, _) in zip(rows[1:], MIXED, strict=True):
        mailing = [row[2], *row[4:10]]
        if usable:
            assert row[2] == "Aaron and Isabelle Williams" and row[4] == "1 Sample St"
        else:
            assert mailing == [""] * 7
        # Family, heads and code are always there for follow-up.
        assert row[3] == "Aaron and Isabelle Williams" and row[10] == "ABCDEFGH"
    assert report.unaddressed == 2
    assert dict(report.metadata)["Families in this file"] == "5"
    assert dict(report.metadata)[UNADDRESSED_DETAIL] == "2"


def test_postal_pdf_blocks_name_a_missing_mailing_address():
    """The PDF keeps an unaddressable Family's block and says why it is empty."""
    report = document(
        [item(), item(family_duid=2, mailable=False, address={})], postal=True
    )
    blocks = list(address_blocks(report))
    assert len(blocks) == 2
    assert blocks[0][0] == "Aaron and Isabelle Williams"
    assert blocks[1][0] == "No usable mailing address"
    assert "ParishSoft DUID 2" in " ".join(blocks[1])


def test_xlsx_and_pdf_carry_the_same_columns_and_details():
    """XLSX has the plain sheet; PDF fits the table with details in the header."""
    report = document([item()])
    output = io.BytesIO()
    render_directory(report, output, format="xlsx")
    book = load_workbook(output)
    sheet = book["Families"]
    assert tuple(cell.value for cell in sheet[1]) == CODE_HEADINGS
    assert sheet["A2"].value == "=Sample Family, Aaron Williams and Isabelle Williams"
    assert sheet["A2"].data_type == "s"
    assert dict(
        (row[0].value, row[1].value) for row in book["Report information"].iter_rows()
    )["Privacy"].startswith("Sensitive: Family codes.")
    book.close()
    line = next(table_lines(report))[0]
    assert "=Sample Family, Aaron Williams and Isabelle Williams" in line
    assert "ABCDEFGH" in line
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
    assert report.unaddressed == 1 and len(report.rows) == 2
    assert report.rows[1][2] == "" and report.rows[1][4:10] == ("",) * 6


def test_testing_note_is_a_report_detail_never_a_csv_row():
    """Testing-mode files say live codes wait for go-live, outside the columns."""
    payload = dict(
        metadata=dict(
            name="Annual campaign",
            id="campaign",
            source_id="source",
            source_generation=1,
            source_as_of=MOMENT.isoformat(),
        ),
        total=1,
        rows=[item()],
    )
    parameters = {
        "filters": {"search": "", "reason": "any", "phone": "any"},
        "postal": False,
        "exact": False,
    }
    common = dict(
        parish_name="Sample Parish",
        captured_at=MOMENT,
        requested_at=MOMENT,
        timezone="UTC",
    )
    testing = directory_document(payload, parameters, testing=True, **common)
    live = directory_document(payload, parameters, **common)
    assert "work only after go-live" in dict(testing.metadata)["Testing mode"]
    assert "Testing mode" not in dict(live.metadata)
    assert csv_rows(testing) == csv_rows(live)
    output = io.BytesIO()
    assert render_directory(testing, output, format="pdf")


@pytest.mark.parametrize("mode,shown", [("testing", True), ("production", False)])
def test_testing_codes_context_follows_the_mode(monkeypatch, mode, shown):
    """The directory and export pages explain live codes only in Testing mode."""
    from types import SimpleNamespace

    from parishkit.stewardship.accounts import campaign_family_test, runtime_models
    from parishkit.stewardship.campaigns import models
    from parishkit.stewardship.reports.directories import testing_codes_context

    runtime = SimpleNamespace(mode=mode)
    monkeypatch.setattr(
        runtime_models.SystemConfiguration,
        "objects",
        SimpleNamespace(first=lambda: runtime),
    )
    monkeypatch.setattr(
        models.Campaign,
        "objects",
        SimpleNamespace(filter=lambda **_: SimpleNamespace(first=lambda: "c")),
    )
    monkeypatch.setattr(
        campaign_family_test, "chosen_family_test_url", lambda *_: "/test-send"
    )
    context = testing_codes_context("campaign")
    assert context["testing_codes"] is shown
    assert context["family_test_url"] == ("/test-send" if shown else None)
