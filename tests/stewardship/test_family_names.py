"""The shared Family naming rule prefers the household surname."""

import re
import sys
from pathlib import Path

import pytest

from parishkit.stewardship.source import family_names
from parishkit.stewardship.source.family_names import (
    family_display_name,
    family_heads_name,
    heads_salutation_name,
    name_placeholders,
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
    """Salutation order (heads_salutation_name), with the surname as the fallback."""
    assert family_heads_name("Squyres", heads, surname_first=False) == expected


@pytest.mark.parametrize(
    ("heads", "expected"),
    [
        # The launch-day request (#468): "Dear Andrew and Betty Test:".
        (
            [{"first": "Andrew", "last": "Test"}, {"first": "Betty", "last": "Test"}],
            "Andrew and Betty Test",
        ),
        # Two surname groups, then three single-head groups.
        (
            [
                {"first": "Andrew", "last": "Test"},
                {"first": "Betty", "last": "Test"},
                {"first": "Carol", "last": "Smith"},
            ],
            "Andrew and Betty Test and Carol Smith",
        ),
        (
            [
                {"first": "Ann", "last": "Lee"},
                {"first": "Bob", "last": "Ray"},
                {"first": "Cy", "last": "Fox"},
            ],
            "Ann Lee, Bob Ray and Cy Fox",
        ),
        # A group sits where its first head appears, in the heads' order.
        (
            [
                {"first": "Ann", "last": "Lee"},
                {"first": "Bob", "last": "Ray"},
                {"first": "Cy", "last": "Lee"},
            ],
            "Ann and Cy Lee and Bob Ray",
        ),
        (
            [{"first": first, "last": "Lee"} for first in ("Ann", "Bob", "Cy")],
            "Ann, Bob and Cy Lee",
        ),
        ([{"first": "Ann", "last": "Lee"}], "Ann Lee"),
        # Missing a first name: the surname still names the group.
        (
            [{"first": "", "last": "Lee"}, {"first": "Bob", "last": "Lee"}],
            "Bob Lee",
        ),
        ([{"first": None, "last": "Lee"}], "Lee"),
        # Missing a last name: the first name stands alone after every surname
        # group, so it never reads as sharing a surname ("Cher Lee").
        (
            [{"first": "Cher", "last": ""}, {"first": "Bob", "last": "Lee"}],
            "Bob Lee and Cher",
        ),
        # Captures made before first and last names were kept separately.
        (
            [{"name": "Old Capture"}, {"first": "Bob", "last": "Lee"}],
            "Bob Lee and Old Capture",
        ),
        # Letter case never splits a group; the first spelling is kept.
        (
            [{"first": "Andrew", "last": "Test"}, {"first": "Betty", "last": "test"}],
            "Andrew and Betty Test",
        ),
        # Surrounding whitespace never splits a group.
        (
            [{"first": " Ann ", "last": "Lee "}, {"first": "Bob", "last": " Lee"}],
            "Ann and Bob Lee",
        ),
        # No usable names at all.
        ([{"first": "", "last": ""}, {"name": None}], ""),
        ([], ""),
    ],
)
def test_heads_salutation_name_groups_first_names_by_surname(heads, expected):
    """First names grouped under each shared surname, groups joined naturally."""
    assert heads_salutation_name(heads) == expected


def test_heads_salutation_name_default_names_a_headless_family():
    """Callers that need a non-empty salutation supply the Family's name."""
    assert heads_salutation_name([], "Family") == "Family"
    assert heads_salutation_name([{"name": " "}], "Lee") == "Lee"


HEADS = [{"first": "Andrew", "last": "Test"}, {"first": "Betty", "last": "Test"}]
MEMBERS = HEADS + [{"first": "Cy", "last": "Test"}]


def test_name_placeholders_distinguish_heads_from_every_member():
    """head_salutation names the heads, all_family_member_names everyone (#471).

    family_member_names is the older name of head_salutation and always
    carries the same value, so stored templates keep greeting the heads.
    """
    assert name_placeholders("Test", HEADS, MEMBERS) == {
        "family_name": "Test",
        "head_salutation": "Andrew and Betty Test",
        "family_member_names": "Andrew and Betty Test",
        "all_family_member_names": "Andrew, Betty and Cy Test",
    }


def test_name_placeholders_fall_back_to_the_family_name():
    """Without usable head (or Member) names the Family is addressed by name."""
    values = name_placeholders("Example", [], [])
    assert values["head_salutation"] == values["family_member_names"] == "Example"
    assert values["all_family_member_names"] == "Example"
    # Heads without names still leave the Members named, and the reverse.
    assert name_placeholders("Example", [{"first": " "}], MEMBERS) == {
        "family_name": "Example",
        "head_salutation": "Example",
        "family_member_names": "Example",
        "all_family_member_names": "Andrew, Betty and Cy Test",
    }


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
