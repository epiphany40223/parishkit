"""The Portal users reads behind the page and ``pk-admin users`` (#952 slice 2).

``users list`` prints each user's email, Name, role and newest sign-in with
the Users table's sort, in either policy form. The Upgrade preview panel
shapes the users upgrade's own preview, Administrators first.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from parishkit.stewardship.accounts.user_reads import (
    UPGRADE_SORTING,
    USER_SORTING,
    upgrade_panel,
    upgrade_rows,
    user_list,
)
from parishkit.stewardship.accounts.users_upgrade import Identity, plan_upgrade

from .policy_factory import address, assignment, domain

EARLY = datetime(2026, 9, 1, 12, tzinfo=UTC)
LATE = datetime(2026, 10, 1, 12, tzinfo=UTC)


def user(email, role, name):
    """One users-form record."""
    return {
        "id": str(uuid4()),
        "values": {"kind": "user", "email": email, "name": name, "role": role},
    }


def seen(email, last_login):
    """One recorded identity as ``policy_identities`` returns it."""
    return {
        "id": uuid4(),
        "email": email,
        "hosted_domain": None,
        "disabled": False,
        "last_login": last_login,
    }


def test_rules_form_lists_granting_address_rules_with_their_highest_role():
    """Before the upgrade, an exact-address rule granting a role is a user."""
    records = [
        address("staff@example.org", ("ministry_leader", "staff")),
        address("admin@example.org"),
        # A deny rule, a domain rule and an assignment are not users.
        address("denied@example.org", ()),
        domain("example.org", ("staff",)),
        assignment("staff@example.org"),
    ]
    rows = user_list(
        records,
        [
            # Several identities of one address: the newest sign-in counts.
            seen("staff@example.org", EARLY),
            seen("staff@example.org", LATE),
            seen("staff@example.org", None),
        ],
    )
    assert rows == [
        {
            "email": "admin@example.org",
            "name": None,
            "role": "administrator",
            "last_login": None,
        },
        {
            "email": "staff@example.org",
            "name": None,
            "role": "staff",
            "last_login": LATE,
        },
    ]


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


def _plan():
    """A sample preview: two Administrators, a domain rule, a deny rule."""
    records = [
        address("admin@example.org"),
        address("second@example.org", ("administrator", "staff")),
        address("denied@example.org", ()),
        domain("example.org", ("ministry_leader", "staff")),
    ]
    identities = [
        Identity("helper@example.org", "example.org", False, True),
        Identity("never@example.org", "example.org", False, False),
        Identity("off@example.org", "example.org", True, True),
        Identity("denied@example.org", "example.org", False, True),
    ]
    names = {"helper@example.org": ["Helper, Hal"]}
    return {"already_users": False} | plan_upgrade(records, identities, names)


def test_upgrade_rows_name_the_rule_each_user_comes_from():
    """Each converted user says whether an address or a domain rule gave it."""
    rows = {row["email"]: row for row in upgrade_rows(_plan())}
    assert set(rows) == {
        "admin@example.org",
        "second@example.org",
        "helper@example.org",
    }
    assert rows["helper@example.org"]["name"] == "Helper, Hal"
    assert rows["helper@example.org"]["role"] == "staff"
    assert rows["helper@example.org"]["source"] == {
        "kind": "domain",
        "domain": "example.org",
    }
    assert rows["helper@example.org"]["source_label"] == (
        "Hosted-domain rule example.org"
    )
    second = rows["second@example.org"]
    assert second["source"]["kind"] == "address"
    assert [str(label) for label in second["source"]["roles"]] == [
        "Administrator",
        "Staff",
    ]
    assert str(second["role_label"]) == "Administrator"
    # The preview table sorts like the Users table, Administrators first.
    ordered = UPGRADE_SORTING.sort_rows(list(rows.values()), "-role")
    assert [row["email"] for row in ordered][-1] == "helper@example.org"


def test_upgrade_panel_lists_administrators_first_and_what_is_dropped():
    """The panel names the kept Administrators exactly, and nothing is hidden."""
    panel = upgrade_panel(_plan())
    assert panel["administrators"] == ["admin@example.org", "second@example.org"]
    assert [
        (rule["domain"], rule["count"], str(rule["role"]))
        for rule in panel["domain_rules"]
    ] == [("example.org", 1, "Staff")]
    assert panel["dropped"] == [
        {"email": "denied@example.org", "overrides_identity": True, "name": "denied"}
    ]
    assert panel["never_signed_in"] == ["never@example.org"]
    assert panel["disabled"] == ["off@example.org"]


def test_no_panel_once_the_configuration_holds_users():
    """A configuration already in the users form has nothing to preview."""
    assert upgrade_panel({"already_users": True}) is None
