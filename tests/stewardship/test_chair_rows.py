"""Database-free Chairperson suggestion rows: grouped, ordered and never guessed."""

from parishkit.stewardship.accounts.chair_rows import suggestion_rows
from parishkit.stewardship.accounts.user_rows import AppliedPolicy

from .policy_factory import address, assignment, domain


def relationship(**values):
    """One current source relationship as the projection reports it."""
    return {
        "member_duid": 3,
        "member_name": "Member Middle Example",
        "ministry_duid": 4,
        "ministry_name": "Choir",
        "email": "valid@example.org",
        "publish_email": False,
        "address_members": [3],
    } | values


def text(values):
    """Lazy translations compare as the text an Administrator reads."""
    return [str(value) for value in values]


def test_rows_group_by_address_and_ministry_in_ministry_order():
    """Several roster rows are one candidate; Ministries sort by name, not DUID."""
    policy = AppliedPolicy([address()], [])
    rows = suggestion_rows(
        policy,
        [
            relationship(ministry_duid=9, ministry_name="zeta"),
            relationship(),
            relationship(),
            relationship(email="second@example.org", address_members=[3]),
        ],
        active=frozenset({4, 9}),
    )
    assert [(row["ministry_name"], row["email"]) for row in rows] == [
        ("Choir", "second@example.org"),
        ("Choir", "valid@example.org"),
        ("zeta", "valid@example.org"),
    ]
    row = rows[1]
    assert row["candidates"] == [
        {"duid": 3, "name": "Member Middle Example", "publishable": False}
    ]
    assert row["owners"] == 1 and not row["ambiguous"]
    assert row["rule"] == {
        "kind": None,
        "roles": [],
        "deny": False,
        "suspended": False,
    }
    assert row["assignments"] == []
    assert str(row["source"]) == "Parish source Chairperson"


def test_a_ministry_without_a_name_sorts_and_shows_empty():
    """A nameless Ministry payload is valid source and cannot break the page."""
    rows = suggestion_rows(
        AppliedPolicy([address()], []),
        [relationship(ministry_duid=9, ministry_name=None), relationship()],
        active=frozenset({4, 9}),
    )
    assert [(row["ministry_name"], row["ministry_duid"]) for row in rows] == [
        ("", 9),
        ("Choir", 4),
    ]


def test_inactive_ministries_are_not_suggested():
    """Local activity decides; a Chairperson of an inactive Ministry is omitted."""
    assert (
        suggestion_rows(
            AppliedPolicy([address()], []), [relationship()], active=frozenset()
        )
        == []
    )


def test_shared_addresses_are_ambiguous_and_keep_every_member():
    """Two Chairpersons on one address, or another Member using it, are shown."""
    policy = AppliedPolicy([address()], [])
    (shared, other) = suggestion_rows(
        policy,
        [
            relationship(address_members=[3, 6]),
            relationship(
                member_duid=6, member_name="Another Example", address_members=[3, 6]
            ),
            relationship(
                email="also@example.org",
                ministry_duid=9,
                ministry_name="Ushers",
                address_members=[3, 8],
            ),
        ],
        active=frozenset({4, 9}),
    )
    assert [row["duid"] for row in shared["candidates"]] == [6, 3]
    assert shared["owners"] == 2 and shared["ambiguous"]
    # One Chairperson, but a second active Member uses the address.
    assert [row["duid"] for row in other["candidates"]] == [3]
    assert other["owners"] == 2 and other["ambiguous"]


def test_current_rule_and_assignment_are_shown_from_policy():
    """What the address already has comes from the applied records alone."""
    records = [
        address(),
        domain("example.org", roles=("staff",)),
        address("valid@example.org", ("ministry_leader",), seeded=True),
        assignment("valid@example.org", ministry=4, seeded=True),
        address("deny@example.org", roles=()),
    ]
    policy = AppliedPolicy(records, [])
    exact, deny, inherited = suggestion_rows(
        policy,
        [
            relationship(),
            relationship(
                email="deny@example.org", ministry_duid=9, ministry_name="Ushers"
            ),
            relationship(
                email="new@example.org", ministry_duid=9, ministry_name="Ushers"
            ),
        ],
        active=frozenset({4, 9}),
    )
    # No overlay confirms the seed: the evaluator grants the seeded address
    # nothing, so the rule shows its role suspended, not held, and the
    # assignment reads as suspended.
    assert exact["rule"]["kind"] == "address" and exact["rule"]["roles"] == []
    assert exact["rule"]["suspended"] is True and exact["rule"]["deny"] is False
    (seed,) = exact["assignments"]
    assert str(seed["source"]) == "Parish source Chairperson"
    assert seed["active"] is False
    assert deny["rule"] == {
        "kind": "address",
        "roles": [],
        "deny": True,
        "suspended": False,
    }
    # A domain rule's roles are conditional on the claim, never held.
    assert inherited["rule"]["kind"] == "domain"
    assert inherited["rule"]["domain"] == "example.org"
    assert text(inherited["rule"]["roles"]) == ["Staff"]
    assert inherited["assignments"] == []
    # A confirmed seed, or a manual grant, shows the role as granted.
    (confirmed,) = suggestion_rows(
        AppliedPolicy(records, [], active_seeded=frozenset({records[3]["id"]})),
        [relationship()],
        active=frozenset({4}),
    )
    assert text(confirmed["rule"]["roles"]) == ["Ministry leader"]
    assert confirmed["rule"]["suspended"] is False
    assert confirmed["assignments"][0]["active"] is True


def test_manual_and_seeded_assignments_are_both_shown_in_fixed_order():
    """A manual assignment beside a suspended seed grants scope; neither hides."""
    seed = assignment("valid@example.org", ministry=4, seeded=True)
    manual = assignment("valid@example.org", ministry=4)
    rule = address("valid@example.org", ("ministry_leader",), seeded=True)
    for records in ([address(), rule, seed, manual], [address(), rule, manual, seed]):
        (row,) = suggestion_rows(
            AppliedPolicy(records, []), [relationship()], active=frozenset({4})
        )
        shown = [(str(item["source"]), item["active"]) for item in row["assignments"]]
        assert shown == [
            ("Administrator entry", True),
            ("Parish source Chairperson", False),
        ]
        # The manual assignment keeps the seeded role in force.
        assert text(row["rule"]["roles"]) == ["Ministry leader"]
        assert row["rule"]["suspended"] is False
