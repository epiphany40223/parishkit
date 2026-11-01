"""The shared Family naming rule prefers the household surname."""

import re
import sys
from pathlib import Path

import pytest

from parishkit.stewardship.source import family_names
from parishkit.stewardship.source.family_names import (
    family_display_name,
    family_heads_name,
    family_last_name,
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
            "Squyres / Smith, Tracy / Jeff",
        ),
        (
            [{"first": first, "last": "Squyres"} for first in "ABC"],
            "Squyres, A, B and C",
        ),
        # Letter case never makes another surname: the name is unchanged.
        (
            [
                {"first": "Tracy", "last": "Squyres"},
                {"first": "Jeff", "last": "SQUYRES"},
            ],
            "Squyres, Tracy and Jeff",
        ),
        # Heads without a last name, beside heads who share the surname.
        (
            [{"first": "Pat", "last": ""}, {"first": "Tracy", "last": "Squyres"}],
            "Squyres, Pat and Tracy",
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
    """Surname, then first names, when every head shares the surname."""
    assert family_heads_name("Squyres", heads) == expected


def head(first, last):
    """One head as family_heads_name reads it."""
    return {"first": first, "last": last}


# The mixed-surname cases of #2320, each (Family surname, heads, staff-list
# name, greeting). The PostgreSQL name function tests hold SQL to the same
# staff-list names (MIXED_SURNAME_CASES in database/family_name_cases.py).
MIXED_SURNAMES = [
    # Two surnames: the Family surname's head first, wherever it is listed.
    (
        "Kelly",
        [head("Linda", "Thieneman"), head("Maureen", "Kelly")],
        "Kelly / Thieneman, Maureen / Linda",
        "Maureen Kelly and Linda Thieneman",
    ),
    # Three heads in two groups.
    (
        "Kelly",
        [head("Maureen", "Kelly"), head("Linda", "Thieneman"), head("Bob", "Kelly")],
        "Kelly / Thieneman, Maureen and Bob / Linda",
        "Maureen and Bob Kelly and Linda Thieneman",
    ),
    # A head without a last name follows the groups, in full.
    (
        "Kelly",
        [head("Pat", ""), head("Linda", "Thieneman"), head("Maureen", "Kelly")],
        "Kelly / Thieneman, Maureen / Linda / Pat",
        "Maureen Kelly, Linda Thieneman and Pat",
    ),
    (
        "Kelly",
        [{"name": "Old Capture"}, head("Linda", "Thieneman")],
        "Thieneman, Linda / Old Capture",
        "Linda Thieneman and Old Capture",
    ),
    # No head shares the Family surname: only the heads' own surnames.
    (
        "Kelly",
        [head("Ann", "Lee"), head("Bo", "Fox")],
        "Lee / Fox, Ann / Bo",
        "Ann Lee and Bo Fox",
    ),
    # Surnames group ignoring letter case; the first spelling is kept (the
    # Family's own spelling for its surname).
    (
        "Kelly",
        [head("Ann", "LEE"), head("Bob", "kelly"), head("Cy", "Lee")],
        "Kelly / LEE, Bob / Ann and Cy",
        "Bob kelly and Ann and Cy LEE",
    ),
    # A surname whose heads have no first names leaves no empty part.
    (
        "Kelly",
        [head("Maureen", "Kelly"), head(" ", "Thieneman")],
        "Kelly / Thieneman, Maureen",
        "Maureen Kelly and Thieneman",
    ),
    # Every head shares the surname: unchanged.
    (
        "Squyres",
        [head("Tracy", "Squyres"), head("Jeff", "Squyres")],
        "Squyres, Tracy and Jeff",
        "Tracy and Jeff Squyres",
    ),
]


@pytest.mark.parametrize(("surname", "heads", "staff", "greeting"), MIXED_SURNAMES)
def test_mixed_surnames_lead_the_staff_list_and_follow_in_the_greeting(
    surname, heads, staff, greeting
):
    """The staff-list form and the greeting for heads of different surnames
    (#2320); family_heads_name's natural order is the greeting."""
    assert family_heads_name(surname, heads) == staff
    assert heads_salutation_name(heads, surname=surname) == greeting
    assert family_heads_name(surname, heads, surname_first=False) == greeting


# A Family with a blank lastName (#2320, Administrator 2026-10-31), each
# (stand-in surname, heads, staff-list name): the heads' own surnames lead,
# as when no head shares the Family surname, even when the stand-in equals
# a head's surname. Only without any head surname does the stand-in lead.
BLANK_LAST_NAME = [
    ("The Lees", [head("Ann", "Lee"), head("Bo", "Lee")], "Lee, Ann and Bo"),
    ("The Lees", [head("Ann", "Lee"), head("Bo", "Fox")], "Lee / Fox, Ann / Bo"),
    ("Fox", [head("Ann", "Lee"), head("Bo", "fox")], "Lee / fox, Ann / Bo"),
    ("Lee", [head("Ann", "Lee"), head("Cher", "")], "Lee, Ann / Cher"),
    ("The Lees", [head("Ann", ""), {"name": "Bo"}], "The Lees, Ann and Bo"),
    ("Pat", [], "Pat"),
]


@pytest.mark.parametrize(("surname", "heads", "staff"), BLANK_LAST_NAME)
def test_a_blank_last_name_lets_the_heads_surnames_lead(surname, heads, staff):
    """Without a recorded lastName the stand-in matches no head (#2320)."""
    assert family_heads_name(surname, heads, recorded=False) == staff


def test_family_last_name_reads_only_a_recorded_last_name():
    """family_last_name is the stripped lastName, else "" (never a stand-in)."""
    assert family_last_name({"lastName": " Lee\u00a0", "mailingName": "X"}) == "Lee"
    assert family_last_name({"lastName": 7, "mailingName": "Lee"}) == ""
    assert family_last_name({"mailingName": "The Lees"}) == ""


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


def test_name_placeholders_greet_the_family_surname_first():
    """Email and Family pages greet the heads of the Family surname first
    (#2320), heads and Members alike."""
    heads = [head("Linda", "Thieneman"), head("Maureen", "Kelly")]
    values = name_placeholders("Kelly", heads, [*heads, head("Cy", "Kelly")])
    assert values["head_salutation"] == "Maureen Kelly and Linda Thieneman"
    assert (
        values["all_family_member_names"] == "Maureen and Cy Kelly and Linda Thieneman"
    )


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
