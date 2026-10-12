"""The archive import's pure parts (#1018, slice 2), without a database."""

import pytest

from parishkit.stewardship.archive.imports import (
    ArchiveRefusal,
    ReplaceNotAcknowledged,
    clean_file_name,
    place,
    year_from_values,
)


@pytest.mark.parametrize(
    ("values", "year"),
    [
        # The year label wins when it is one of the period's years.
        (
            {
                "year_label": "2027",
                "financial": {"start": "2026-07-01", "end": "2027-06-30"},
            },
            2027,
        ),
        (
            {
                "year_label": "2026",
                "financial": {"start": "2026-07-01", "end": "2027-06-30"},
            },
            2026,
        ),
        # Production's "Stewardship 2027", held in autumn 2026.
        (
            {
                "year_label": "2027",
                "financial": {"start": "2027-01-01", "end": "2027-12-31"},
            },
            2027,
        ),
        ({"year_label": " 2027 ", "financial": None}, 2027),
        # Without one, the upcoming financial period's start year.
        ({"year_label": "", "financial": {"start": "2027-01-01"}}, 2027),
        ({"financial": {"start": "2026-07-01", "end": "2027-06-30"}}, 2026),
        # A label outside a one-year period names the year the campaign runs
        # in (the local seed's 2026 on a 2027 period), not the pledge year.
        (
            {
                "year_label": "2026",
                "financial": {"start": "2027-01-01", "end": "2027-12-31"},
            },
            2027,
        ),
    ],
)
def test_the_current_stewardship_year(values, year):
    """The label within the financial period, else the period's year."""
    assert year_from_values(values) == year


@pytest.mark.parametrize(
    "values",
    [
        # A range or any other wording is not one year.
        {"year_label": "2026–2027", "financial": {"start": "2026-07-01"}},
        {"year_label": "FY27", "financial": None},
        # A label outside a period spanning two years: neither year is sure.
        {
            "year_label": "2025",
            "financial": {"start": "2026-07-01", "end": "2027-06-30"},
        },
        # No label and no financial period: the campaign's own start date is
        # the year it runs in, so it is never used.
        {"year_label": "", "financial": None, "start_date": "2026-09-01"},
        {"start_date": "2026-09-01"},
    ],
)
def test_an_unresolvable_year_refuses_every_import(values):
    """A refusal that names Campaign settings, never a guessed year."""
    with pytest.raises(ArchiveRefusal) as refused:
        year_from_values(values)
    [problem] = refused.value.problems
    assert problem["code"] == "year_unresolved"
    assert "Campaign settings" in problem["rule"]


def test_refusal_fields_name_cells_never_values():
    """The command line's fields carry a problem's place and rule only."""
    refusal = ArchiveRefusal(
        [
            {
                "code": "money",
                "sheet": "families",
                "cell": "J17",
                "rule": "annual_pledge must be greater than zero",
            },
            {"code": "header", "sheet": "daily", "cell": None, "rule": "expected x"},
            {"code": "file_size", "sheet": None, "cell": None, "rule": "too big"},
        ],
        more=2,
    )
    assert [field["field"] for field in refusal.fields] == [
        "families!J17",
        "daily",
        "file",
    ]
    assert refusal.more == 2
    assert place({"sheet": None}) == ""
    acknowledgement = ReplaceNotAcknowledged(2025)
    assert acknowledgement.problems[0]["code"] == "replace_not_acknowledged"
    assert "2025" in acknowledgement.problems[0]["rule"]


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("stewardship-2025.xlsx", "stewardship-2025.xlsx"),
        ("C:\\Users\\me\\stewardship-2025.xlsx", "stewardship-2025.xlsx"),
        ("/home/me/stewardship-2025.xlsx", "stewardship-2025.xlsx"),
        ("bad\x00name\n.xlsx", "badname.xlsx"),
        ("", ""),
        (None, ""),
        ("x" * 300, "x" * 255),
    ],
)
def test_stored_file_names_are_cleaned(given, stored):
    """Only the last path part, printable, at most 255 characters."""
    assert clean_file_name(given) == stored
