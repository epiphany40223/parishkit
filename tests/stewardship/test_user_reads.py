"""The Portal users reads behind the page and ``pk-admin users`` (#952 slice 2).

``users list`` prints, and the Users table shows, each user's ID, email,
Name, role, newest sign-in and recorded identities with the Users table's
sort, in either policy form.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.user_reads import USER_SORTING, user_list

from .policy_factory import address, assignment, domain

EARLY = datetime(2026, 9, 1, 12, tzinfo=UTC)
LATE = datetime(2026, 10, 1, 12, tzinfo=UTC)


def user(email, role, name):
    """One users-form record."""
    return {
        "id": str(uuid4()),
        "values": {"kind": "user", "email": email, "name": name, "role": role},
    }


def seen(email, last_login, *, disabled=False):
    """One recorded identity as ``policy_identities`` returns it."""
    return {
        "id": uuid4(),
        "email": email,
        "hosted_domain": None,
        "disabled": disabled,
        "last_login": last_login,
    }


def test_rules_form_lists_granting_address_rules_with_their_highest_role():
    """Before the upgrade, an exact-address rule granting a role is a user."""
    staff, admin = (
        address("staff@example.org", ("ministry_leader", "staff")),
        address("admin@example.org"),
    )
    records = [
        staff,
        admin,
        # A deny rule, a domain rule and an assignment are not users.
        address("denied@example.org", ()),
        domain("example.org", ("staff",)),
        assignment("staff@example.org"),
    ]
    identities = [
        # Several identities of one address: the newest sign-in counts.
        seen("staff@example.org", EARLY),
        seen("staff@example.org", LATE),
        seen("staff@example.org", None, disabled=True),
    ]
    rows = user_list(records, identities)
    assert rows == [
        {
            "id": admin["id"],
            "email": "admin@example.org",
            "name": None,
            "role": "administrator",
            "last_login": None,
            "identities": [],
            "address_match": None,
        },
        {
            "id": staff["id"],
            "email": "staff@example.org",
            "name": None,
            "role": "staff",
            "last_login": LATE,
            # Every recorded identity of the address, in ID order, for the
            # row's sign-in activity and Edit user's disabled line.
            "identities": sorted(
                (
                    {"id": str(item["id"]), "disabled": item["disabled"]}
                    for item in identities
                ),
                key=lambda item: item["id"],
            ),
            "address_match": None,
        },
    ]


def test_a_matched_account_counts_for_the_user_it_matches():
    """A Google account matched through another verified address (#2343) is
    one of the user's identities: its sign-in counts for Last successful
    sign-in, and the user's row names the standing match."""
    staff = user("staff@example.org", "staff", "Staff")
    matched = seen("staff@example.org", LATE) | {
        "match": {
            "match_id": str(uuid4()),
            "id": "account",
            "sign_in_email": "person@personal.example",
            "matched_at": EARLY,
            "confirmed_at": LATE,
        }
    }
    rows = user_list([staff], [seen("staff@example.org", EARLY), matched])
    assert rows[0]["last_login"] == LATE
    assert rows[0]["address_match"] == matched["match"]
    assert len(rows[0]["identities"]) == 2


def test_users_form_lists_every_user_with_its_name():
    """Once converted, every user record is listed with its own Name."""
    rows = user_list(
        [
            user("leader@example.org", "ministry_leader", "Smith, Ann"),
            user("admin@example.org", "administrator", "Admin"),
        ],
        [seen("leader@example.org", EARLY)],
    )
    assert [(row["email"], row["name"], row["role"]) for row in rows] == [
        ("admin@example.org", "Admin", "administrator"),
        ("leader@example.org", "Smith, Ann", "ministry_leader"),
    ]
    assert rows[1]["last_login"] == EARLY


def test_users_sort_as_the_users_table():
    """Email by default; Role by level, Administrator first; never signed in last."""
    rows = user_list(
        [
            user("b@example.org", "ministry_leader", "Brown, Bo"),
            user("a@example.org", "administrator", "Adams, Al"),
            user("c@example.org", "staff", "Clark, Cy"),
        ],
        [seen("b@example.org", EARLY), seen("c@example.org", LATE)],
    )

    def order(token):
        """The addresses in the order this sort token gives."""
        return [row["email"][0] for row in USER_SORTING.sort_rows(rows, token)]

    assert USER_SORTING.parse({}) == "email"
    assert order("email") == ["a", "b", "c"]
    assert order("-email") == ["c", "b", "a"]
    # Role and sign-in start highest and newest: their first-choice token is
    # the descending one, and the heading's second choice reverses it.
    first = {}
    for token, (column, _) in USER_SORTING.tokens.items():
        first.setdefault(column, token)
    assert first == {
        "email": "email",
        "name": "name",
        "role": "-role",
        "last_login": "-last_login",
    }
    assert order("-role") == ["a", "c", "b"]
    assert order("role") == ["b", "c", "a"]
    assert order("-last_login") == ["c", "b", "a"]
    # Never signed in sorts last in either direction.
    assert order("last_login") == ["b", "c", "a"]
    assert order("name") == ["a", "b", "c"]
    with pytest.raises(ValueError):
        USER_SORTING.parse({"sort": "warnings"})
