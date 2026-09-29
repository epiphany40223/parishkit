"""The shared Family naming rule prefers the household surname."""

import re
import sys
from pathlib import Path

import pytest

from parishkit.stewardship.source import family_names
from parishkit.stewardship.source.family_names import (
    family_display_name,
    family_heads_name,
    name_series,
)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        # ParishSoft's mailing name is free text; for some households it is a
        # single given name, which must not become the Family's name.
        ({"lastName": " Surname ", "mailingName": "Anna"}, "Surname"),
        ({"mailingName": "The Household", "lastName": ""}, "The Household"),
        ({"firstName": "Given", "lastName": None}, "Given"),
        ({}, ""),
        ({"lastName": 7, "mailingName": ["x"]}, ""),
    ],
)
def test_family_display_name_prefers_last_name(values, expected):
    """Surname, then mailing name, then first/last, then the default."""
    assert family_display_name(values) == expected


def test_family_display_name_default():
    """Callers that need a non-empty label supply their own default."""
    assert family_display_name({}, "Family") == "Family"


@pytest.mark.parametrize(
    ("heads", "expected"),
    [
        ([], "Squyres"),
        ([{"first": "Tracy", "last": "Squyres"}], "Squyres, Tracy"),
        (
            [
                {"first": "Tracy", "last": "Squyres"},
                {"first": "Jeff", "last": "Squyres"},
            ],
            "Squyres, Tracy and Jeff",
        ),
        (
            [{"first": "Tracy", "last": "Squyres"}, {"first": "Jeff", "last": "Smith"}],
            "Squyres, Tracy and Jeff Smith",
        ),
        (
            [{"first": first, "last": "Squyres"} for first in "ABC"],
            "Squyres, A, B and C",
        ),
        # Captures made before first and last names were kept separately.
        (
            [{"name": "Old Capture"}, {"name": "Second Head"}],
            "Squyres, Old Capture and Second Head",
        ),
        # A head without a usable name adds nothing.
        ([{"first": " ", "last": "Squyres"}, {"first": None, "last": 3}], "Squyres"),
    ],
)
def test_family_heads_name_leads_with_the_surname(heads, expected):
    """Surname, then first names; a head of another surname in full."""
    assert family_heads_name("Squyres", heads) == expected


@pytest.mark.parametrize(
    ("parts", "expected"),
    [([], ""), (["A"], "A"), (["A", "", "B"], "A and B"), (list("ABC"), "A, B and C")],
)
def test_name_series_joins_naturally(parts, expected):
    """Natural English joins, skipping blanks."""
    assert name_series(parts) == expected


@pytest.mark.parametrize(
    ("heads", "expected"),
    [
        ([], "Squyres"),
        (
            [
                {"first": "Jeff", "last": "Squyres"},
                {"first": "Tracy", "last": "Squyres"},
            ],
            "Jeff and Tracy Squyres",
        ),
        (
            [{"first": "Tracy", "last": "Squyres"}, {"first": "Jeff", "last": "Smith"}],
            "Tracy Squyres and Jeff Smith",
        ),
        ([{"first": first, "last": "Ng"} for first in "ABC"], "A, B and C Ng"),
        (
            [{"name": "Old Capture"}, {"name": "Second Head"}],
            "Old Capture and Second Head",
        ),
        # A head without a first name adds nothing, as in surname-first order.
        (
            [{"first": "", "last": "Smith"}, {"first": "Jeff", "last": "Smith"}],
            "Jeff Smith",
        ),
        ([{"first": "", "last": "Smith"}, {"first": " ", "last": "Smith"}], "Smith"),
    ],
)
def test_family_heads_name_can_put_first_names_first(heads, expected):
    """Envelope order: shared surname once at the end, else each full name."""
    assert family_heads_name("Squyres", heads, surname_first=False) == expected


def test_directory_sql_trims_exactly_what_python_strips():
    """The directory SQL's name_trim set equals str.strip()'s whitespace.

    Search and sort use the SQL-built name, the page shows the Python-built
    one; any character one side trims and the other keeps makes them differ.
    """
    sql = (
        Path(family_names.__file__).parents[1] / "schema" / "directory_reports.sql"
    ).read_text()
    literal = re.search(r"name_trim AS \(.*?SELECT E'(.*?)'::text AS ws", sql, re.S)
    assert literal is not None
    trimmed = set(literal.group(1).encode("latin-1").decode("unicode_escape"))
    python = {chr(code) for code in range(sys.maxunicode + 1) if chr(code).isspace()}
    assert trimmed == python
