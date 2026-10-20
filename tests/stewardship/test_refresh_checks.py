"""The refresh checks' pure parts (#1689): run lengths and what is owed.

The writes and reads themselves run against PostgreSQL in
``database/test_refresh_checks_postgresql.py``.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from parishkit.stewardship.pipeline import RefreshOutcome, Stage, refresh_outcome
from parishkit.stewardship.responses.comparison import ValueKind
from parishkit.stewardship.responses.merge import KnownValue
from parishkit.stewardship.responses.refresh_checks import (
    CHECKED_FAMILY_FIELDS,
    CHECKED_FIELDS,
    STORED,
    CheckState,
    _address_key,
    _census_same,
    _mailing_primary,
    _same,
    census_source,
    owed,
    summaries,
)

START = datetime(2026, 10, 30, tzinfo=UTC)


def at(hours):
    """A check time ``hours`` after the Not seen start."""
    return START + timedelta(hours=hours)


def test_unseen_counts_full_unchanged_checks_after_the_start_and_mark():
    """Only full refreshes after the start and the item's mark count, and
    any other outcome restarts the run."""
    start = SimpleNamespace(started_at=START, not_seen_after=2)
    checks = [
        ("a", at(-2), "unchanged", True, None),
        ("a", at(1), "unchanged", False, None),
        ("a", at(2), "unchanged", True, None),
        ("b", at(1), "unchanged", True, None),
        ("b", at(2), "entered_incorrectly", True, {"value": "x"}),
        ("b", at(3), "unchanged", True, None),
        ("c", at(1), "unchanged", True, None),
        ("c", at(3), "unchanged", True, None),
    ]
    states = summaries(checks, start, {"c": at(2)})
    assert states["a"] == CheckState("unchanged", None, 1, 2)
    assert states["b"] == CheckState("unchanged", None, 1, 2)
    assert states["c"].unseen == 1
    assert not states["a"].not_seen(True)
    assert summaries(checks, None, {})["a"].not_seen_after is None


def test_not_seen_needs_a_mark_the_threshold_and_the_start():
    """The flag is a marked item's, at the threshold, while the start row is
    there."""
    state = CheckState("unchanged", None, 2, 2)
    assert state.not_seen(True) and not state.not_seen(False)
    assert not CheckState("unchanged", None, 2, None).not_seen(True)
    assert not CheckState("confirmed", None, 5, 2).not_seen(True)


def test_a_check_is_owed_only_when_something_changed():
    """A changed outcome, a new wrong value, or a marked item's full refresh
    until Not seen; nothing for a quiet refresh."""
    quiet = CheckState(not_seen_after=2)
    assert not owed("unchanged", quiet, full=False, marked=True)
    assert not owed("unchanged", quiet, full=True, marked=False)
    assert owed("unchanged", quiet, full=True, marked=True)
    assert not owed(
        "unchanged", CheckState("unchanged", None, 2, 2), full=True, marked=True
    )
    assert owed("confirmed", quiet, full=False, marked=False)
    wrong = CheckState("entered_incorrectly", {"value": "a"}, 0, 2)
    assert not owed("entered_incorrectly", wrong, full=True, marked=True)
    assert owed("entered_incorrectly", wrong, full=True, marked=True, moved=True)


def test_census_comparison_is_canonical_and_availability_aware():
    """Emails compare without case, not as raw text; an unread value equals
    only another unread one."""
    same = _census_same(ValueKind.EMAIL)
    assert same(
        KnownValue(True, "Ann@Example.org"), KnownValue(True, "ann@example.org")
    )
    assert not same(
        KnownValue(True, "ann@example.org"), KnownValue(True, "bo@example.org")
    )
    assert same(KnownValue(False), KnownValue(False))
    assert not same(KnownValue(False), KnownValue(True, None))


def test_checked_fields_are_the_confirmable_member_fields():
    """The contact read's fields, a death date, and (#1738) a move and a
    death; the Family's opt-out and (#1749) home address, never its mailing
    address. A New Member is checked as its own kind (``CHECKED``)."""
    assert {"prefix", "email", "death_date"} <= CHECKED_FIELDS
    assert {"moved_household", "deceased_status"} <= CHECKED_FIELDS
    assert "new_member" not in CHECKED_FIELDS
    assert {"email_opt_out", "home_address"} == CHECKED_FAMILY_FIELDS


def member(*, family="1", status="Active", **values):
    """One corpus Member 3 of Family ``family``, as normalization builds it."""
    return {
        "memberDUID": 3,
        "memberStatus": status,
        "family_key": family,
        "active": status not in {"Inactive", "Deceased"},
        "deceased": status == "Deceased",
        **values,
    }


def corpus(found=None, *, family=None):
    """A corpus holding Member 3 (``found``) and Family 1 (``family``), if given."""
    return {
        "member": {} if found is None else {"3": found},
        "contact": {},
        "family": {} if family is None else {"1": family},
    }


def primary(fields):
    """The snapshot's primary address record of Family 1 with ``fields``."""
    return {
        "owner_kind": "family",
        "owner_key": "1",
        "kind": "primary",
        "fields": fields,
    }


def change(field, kind="member", key="3", **extra):
    """A census change of ``field``, annotated with its Family's DUID."""
    return SimpleNamespace(
        entity_kind=kind, entity_key=key, field=field, family_duid=1, **extra
    )


@pytest.mark.parametrize(
    "found,expected",
    [
        (member(), KnownValue(True, False)),
        (member(family="2"), KnownValue(True, True)),
        (member(status="Inactive"), KnownValue(True, True)),
        (None, KnownValue(True, True)),
        (member(status="Deceased"), None),
        (member(family="2", status="Deceased"), None),
    ],
)
def test_a_move_reads_whether_the_member_left_the_family(found, expected):
    """Left the Family when not an active Member of it: gone, moved or
    inactive. A death is no evidence of a move."""
    assert census_source(change("moved_household"), corpus(found)) == expected


@pytest.mark.parametrize(
    "found,expected",
    [
        (member(), KnownValue(True, False)),
        (member(status="Deceased"), KnownValue(True, True)),
        (member(status="Inactive"), KnownValue(True, False)),
        (member(family="2", status="Deceased"), None),
        (None, None),
    ],
)
def test_a_death_reads_the_deceased_flag_inside_the_family(found, expected):
    """Only a Member still listed in the change's Family is evidence."""
    assert census_source(change("deceased_status"), corpus(found)) == expected


@pytest.mark.parametrize(
    "family,key,expected",
    [
        ({"active": True, "sendNoMail": True}, "1", KnownValue(True, True)),
        ({"active": True, "sendNoMail": False}, "1", KnownValue(True, False)),
        ({"active": True}, "1", None),
        ({"active": True, "sendNoMail": 1}, "1", None),
        ({"active": False, "sendNoMail": True}, "1", None),
        ({"active": True, "sendNoMail": True}, "2", None),
        (None, "1", None),
    ],
)
def test_an_opt_out_reads_the_active_familys_send_no_mail(family, key, expected):
    """The Family's own flag, only while the Family is active and the flag
    is a real boolean."""
    found = census_source(change("email_opt_out", "family", key), corpus(family=family))
    assert found == expected


def test_a_contact_field_needs_an_active_living_member_of_the_family():
    """As reconciliation reads it: none for a Member who left, a death date
    whatever the Member's status."""
    first = change("first_name")
    assert census_source(first, corpus(member(firstName="Ann"))) == KnownValue(
        True, "Ann"
    )
    for gone in (member(family="2"), member(status="Inactive")):
        assert census_source(first, corpus(gone)) is None
    assert census_source(first, corpus()) is None
    died = member(status="Deceased", dateOfDeath="2026-01-01")
    assert census_source(change("death_date"), corpus(died)) == KnownValue(
        True, "2026-01-01"
    )


@pytest.mark.parametrize(
    "found,added,expected",
    [
        (member(), 3, KnownValue(True, True)),
        (member(family="2"), 3, KnownValue(True, False)),
        (member(status="Inactive"), 3, KnownValue(True, False)),
        (member(status="Deceased"), 3, KnownValue(True, False)),
        (None, 3, KnownValue(True, False)),
        (member(), None, None),
    ],
)
def test_a_new_member_reads_the_member_id_recorded_at_its_mark(found, added, expected):
    """Listed when the data has that Member, active and living, in the
    change's Family (the shared status's ``current``); no ID, no evidence
    (#1749)."""
    row = change("new_member", "proposed_member", "local-key", added_duid=added)
    assert census_source(row, corpus(found)) == expected


HOME = {
    "primaryAddress1": "1 Main St",
    "primaryAddress2": "Apt 2",
    "primaryAddress3": "Rear",
    "primaryCity": "Louisville",
    "primaryState": "KY",
    "primaryPostalCode": "40202",
    "primaryZipPlus": "1234",
}


def address(**parts):
    """A census address answer, a Louisville one unless ``parts`` say."""
    return {
        "line1": "1 Main St",
        "line2": "Apt 2 Rear",
        "city": "Louisville",
        "region": "KY",
        "postal_code": "40202",
        "country": "US",
    } | parts


def test_a_home_address_reads_the_active_familys_primary_address():
    """ParishSoft's primary address, lines two and three joined, no country;
    none for an inactive Family or one with no primary address (#1749)."""
    home = change("home_address", "family", "1", primary_address=primary(HOME))
    active = {"active": True}
    assert census_source(home, corpus(family=active)) == KnownValue(
        True, address(country="")
    )
    one = {"primaryAddress1": "2 Oak Ave", "primaryCity": "Louisville"}
    home.primary_address = primary(one)
    assert census_source(home, corpus(family=active)).value == {
        "line1": "2 Oak Ave",
        "line2": "",
        "city": "Louisville",
        "region": "",
        "postal_code": "",
        "country": "",
    }
    home.primary_address = primary(HOME)
    assert census_source(home, corpus(family={"active": False})) is None
    home.primary_address = None
    assert census_source(home, corpus(family=active)) is None
    mailing = change("mailing_address", "family", "1", primary_address=primary(HOME))
    assert census_source(mailing, corpus(family=active)) is None


def test_an_address_compares_without_country_case_or_zip_plus_four():
    """The census compare rules, with the country and a ZIP+4 suffix left
    out, since no read returns them."""
    same = _same("home_address", ValueKind.ADDRESS)
    read = KnownValue(True, address(country=""))
    assert same(read, KnownValue(True, address(postal_code="40202-1234")))
    assert same(read, KnownValue(True, address(city="LOUISVILLE ")))
    assert not same(read, KnownValue(True, address(line1="2 Oak Ave")))
    assert not same(read, KnownValue(True, None))
    assert _address_key(address(postal_code="4020-1234")) != _address_key(
        address(postal_code="4020")
    )


def test_a_home_address_never_reads_entered_incorrectly():
    """A different primary address is unchanged, since the Family's primary
    may be its mailing block: confirmed only when it holds the answer."""
    same = _same("home_address", ValueKind.ADDRESS)
    answer = address()
    for refreshed, expected in (
        (address(country=""), RefreshOutcome.CONFIRMED),
        (address(line1="9 Elm St", country=""), RefreshOutcome.UNCHANGED),
    ):
        current = KnownValue(True, refreshed)
        assert (
            refresh_outcome(
                Stage.ENTERED_BY_HAND,
                refreshed=current,
                answered_against=KnownValue(False),
                carried_forward=KnownValue(True, answer),
                # No third value: the refresh's own value is the mark.
                marked_against=current,
                same=same,
            )
            is expected
        )


def test_a_primary_address_holding_the_mailing_answer_is_no_evidence():
    """ParishSoft's primary is evidently the mailing block when it holds the
    mailing answer and not the home one."""
    mailing = address(line1="PO Box 9", line2="")
    row = change(
        "home_address",
        "family",
        "1",
        admin_value_set=False,
        admin_value=None,
        submitted_value=address(),
        mailing_answer=mailing,
    )
    assert _mailing_primary(row, KnownValue(True, mailing | {"country": ""}))
    assert not _mailing_primary(row, KnownValue(True, address(country="")))
    row.mailing_answer = None
    assert not _mailing_primary(row, KnownValue(True, mailing))
    # A mailing answer the same as home: confirmed, not skipped.
    row.mailing_answer = address()
    assert not _mailing_primary(row, KnownValue(True, address(country="")))


def test_an_updated_change_parishsoft_no_longer_holds_is_a_conflict():
    """Rule 2's exception and rule 3 (#1749): back at A, or at a third value,
    an Updated in ParishSoft change is a Conflict, stored as one."""
    same = _census_same(ValueKind.TEXT)
    for refreshed in ("Ann", "Anna"):
        result = refresh_outcome(
            Stage.UPDATED,
            refreshed=KnownValue(True, refreshed),
            answered_against=KnownValue(True, "Ann"),
            carried_forward=KnownValue(True, "Annie"),
            same=same,
        )
        assert result is RefreshOutcome.CONFLICT
        assert STORED[result] == "conflict"


def test_a_null_primary_address_field_reads_as_blank():
    """ParishSoft returns a blank field as null: it reads as blank, as SQL's
    ``coalesce`` does, and the key never fails on it."""
    home = change(
        "home_address",
        "family",
        "1",
        primary_address=primary(
            {
                "primaryAddress1": "1 Main",
                "primaryAddress2": None,
                "primaryAddress3": None,
                "primaryCity": None,
                "primaryState": None,
                "primaryPostalCode": None,
            }
        ),
    )
    found = census_source(home, corpus(family={"active": True}))
    assert found.value == {
        "line1": "1 Main",
        "line2": "",
        "city": "",
        "region": "",
        "postal_code": "",
        "country": "",
    }
    assert _address_key(found.value) == _address_key(found.value | {"country": "US"})
    assert _address_key(found.value | {"postal_code": None}) == _address_key(
        found.value
    )
