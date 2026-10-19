"""An edited census address meets the Family's own address rules (#1719).

The worklist's Edit can change a Family's home or mailing address. The
service reads the edit as the Family form reads an address
(``census_review.edited_address``, through ``census.validate_address``);
frozen file 0138 makes the review guard hold it, in SQL too, to the rules
the Family's answer meets (``stewardship_response_address_guard_v1``). These
tests forge reviews with the service bypassed, and check that Python and
SQL agree over one list of cases.
"""

import json
from pathlib import Path

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.responses.census_review import InvalidEdit, edited_address

from ..census_factory import address
from .test_census_pipeline_review_postgresql import forge, prepared, state
from .test_publication_worker_postgresql import _replaced

pytestmark = pytest.mark.django_db(transaction=True)

MIGRATIONS = Path(__file__).resolve().parents[3] / (
    "src/parishkit/stewardship/schema/migrations"
)


def _frozen(name):
    """The one frozen file named ``name``, whatever its number.

    A stacked branch's frozen files keep provisional numbers until the
    coordinator numbers them in merge order (an integration build renumbers
    them), so the tests find each file by its name alone.
    """
    (path,) = MIGRATIONS.glob(f"[0-9][0-9][0-9][0-9]_{name}.sql")
    return path


FROZEN = _frozen("census_address_guard")
GUARD = "stewardship_proposal_review_guard_v1"
# The deferred pairing check's message: the guard admitted the review row,
# and only the forged result's missing audit event refuses it.
ADMITTED = "result and audit"


def edit(**parts):
    """An edited address that differs from the Family's (9 Census Way)."""
    return address(**{"line1": "10 Census Way", **parts})


def sized(total):
    """A non-US edit whose JSON is exactly ``total`` bytes as PostgreSQL
    prints it (the review guard's ``octet_length(admin_value::text)``).

    Parts of a three-byte script and a street line with a quote and a
    backslash (each printed escaped) pin the service's count to the guard's.
    """
    value = address(
        line1='10 "Census" \\ Way',
        city="\u20ac" * 100,
        region="\u20ac" * 100,
        country="GB",
    )
    extra = total - len(json.dumps(value, ensure_ascii=False).encode())
    return {**value, "line2": "\u20ac" * (extra // 3) + "x" * (extra % 3)}


# Raw edits as a reviewer types them: the Family form accepts some (perhaps
# after normalizing them) and refuses the rest.
CASES = [
    edit(),
    edit(postal_code="40223-1234"),
    edit(country="us", region="ky"),
    edit(line1="  10   Census Way "),
    edit(region="PR", postal_code="00901"),
    edit(country="CA", region="ON", postal_code="K1A 0B1"),
    edit(country="GB", region="", postal_code=""),
    edit(country="ZZ"),
    edit(country=""),
    edit(region="XX"),
    edit(region=""),
    edit(postal_code="4022"),
    edit(postal_code="40223-12"),
    edit(postal_code="ABCDE"),
    edit(city=""),
    edit(line2="Apt\t2"),
    edit(line2="Apt\n2"),
    edit(city="x" * 101),
    edit(line1="1" + "x" * 200),
    # Each part within its limit, the whole past the guard's 1,024 bytes
    # (two bytes a character).
    address(
        line1="1" + "é" * 199,
        line2="é" * 200,
        city="é" * 100,
        region="é" * 100,
        postal_code="",
        country="GB",
    ),
    # The same script within the whole edit's limit.
    address(line1="1" + "é" * 199, city="é" * 100, country="GB"),
    # At the guard's limit exactly, and one byte past it.
    sized(1024),
    sized(1025),
]


def guard_refusal(proposal, actor, value):
    """Forge an approved edit of ``value``; return the guard's refusal, or None.

    A review the guard admits still fails at commit, on the forged result's
    missing audit event, so nothing is ever stored.
    """
    with pytest.raises(DatabaseError) as error:
        forge(proposal, actor, decision="approved", value_set=True, value=value)
    if ADMITTED in str(error.value):
        return None
    return error.value


# The address guard's two refusals: a part's text, and the address's
# country-aware completeness.
TEXT = "Invalid normalized response address"
COMPLETE = "Incomplete or invalid country-aware response address"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (edit(country="ZZ"), COMPLETE),
        (edit(region="XX"), COMPLETE),
        (edit(postal_code="4022"), COMPLETE),
        (edit(city=""), COMPLETE),
        # Never normalized by the database: the service stores "US", single
        # spaces, no control characters.
        (edit(country="us"), COMPLETE),
        (edit(line1="  10 Census Way"), TEXT),
        (edit(line2="Apt\t2"), TEXT),
    ],
)
def test_sql_refuses_an_edited_address_the_family_form_refuses(
    response_service, value, message
):
    """A forged Edit with an address the Family's own answer could not carry
    (an unknown country, a US address with an unknown state or a bad ZIP, no
    city, text the form would have normalized) is refused with 23514."""
    harness, _, staff, rows = prepared(response_service)
    refusal = guard_refusal(rows["home_address"], staff, json.dumps(value))
    assert refusal is not None
    assert refusal.__cause__.sqlstate == "23514"
    assert message in str(refusal)
    assert state(rows["home_address"])[:3] == ("unreviewed", "pending", False)


def test_sql_admits_an_edited_non_us_address(response_service):
    """A non-US address with no region or postal code, and one with its own
    postal format, pass the guard (only the forged audit is missing)."""
    harness, _, staff, rows = prepared(response_service)
    for value in (
        edit(country="GB", region="", postal_code=""),
        edit(country="CA", region="ON", postal_code="K1A 0B1"),
    ):
        assert guard_refusal(rows["home_address"], staff, json.dumps(value)) is None


def test_python_and_sql_agree_on_edited_addresses(response_service):
    """Over one list of cases, the service and the guard agree: what
    ``edited_address`` accepts, as it stores it, the guard admits; what it
    refuses, the guard refuses as typed; and a value it had to normalize is
    refused as typed too, so only the service's form is ever stored."""
    harness, _, staff, rows = prepared(response_service)
    proposal = rows["home_address"]
    accepted = 0
    for raw in CASES:
        try:
            value = edited_address(raw)
        except InvalidEdit:
            value = None
        typed = guard_refusal(proposal, staff, json.dumps(raw))
        if value is None:
            assert typed is not None, raw
            assert typed.__cause__.sqlstate == "23514", raw
            continue
        accepted += 1
        assert guard_refusal(proposal, staff, json.dumps(value)) is None, raw
        assert (typed is None) == (value == raw), raw
    # The list covers both outcomes.
    assert 0 < accepted < len(CASES)
    # The size boundary falls where the guard puts it, on both sides.
    assert edited_address(sized(1024)) == sized(1024)
    with pytest.raises(InvalidEdit):
        edited_address(sized(1025))


def test_do_block_refuses_0131s_guard_and_installs_its_own():
    """The file's closing check passes as installed and raises once the
    guard is put back to 0131's body; on that state the file installs its
    own body again, not SECURITY DEFINER."""
    text = FROZEN.read_text(encoding="utf-8")
    check = text[text.index("DO $check$") :]
    earlier = _replaced(
        _frozen("census_pipeline_review").read_text(encoding="utf-8"),
        GUARD,
    )
    assert "stewardship_response_address_guard_v1" not in earlier
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(check)
    with (
        pytest.raises(DatabaseError, match="lacks the Family address rules"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(earlier)
        cursor.execute(check)

    class Rollback(Exception):
        """Ends a check that must leave the database as it found it."""

    with pytest.raises(Rollback), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(earlier)
        cursor.execute(text)
        cursor.execute(
            "SELECT prosecdef, prosrc LIKE"
            " '%%stewardship_response_address_guard_v1(NEW.admin_value)%%'"
            " FROM pg_proc WHERE proname=%s",
            [GUARD],
        )
        assert cursor.fetchall() == [(False, True)]
        raise Rollback
