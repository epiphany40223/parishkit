"""Database-free rows for suspended seeds: the evaluator's facts, decisions offered."""

from datetime import UTC, datetime

from parishkit.stewardship.accounts.chair_review_rows import suspended_rows
from parishkit.stewardship.accounts.user_rows import AppliedPolicy

from .policy_factory import address, assignment

OPENED = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 21, 9, 30, tzinfo=UTC)


def review(**values):
    """One open review as the data reader reports it."""
    return {
        "email": "c@example.org",
        "ministry_duid": 4,
        "ministry_name": "Choir",
        "member_duid": 3,
        "reason": "relationship_missing",
        "opened_at": OPENED,
        "generation": 7,
        "latest_at": LATER,
        "elsewhere": False,
    } | values


def text(values):
    """Lazy translations compare as the text an Administrator reads."""
    return [str(value) for value in values]


def test_rows_state_the_suspension_and_what_the_evaluator_grants_now():
    """A suspended seed grants nothing; the row says why and since when."""
    records = [
        address(),
        address("c@example.org", ("ministry_leader",), seeded=True),
        assignment("c@example.org", ministry=4, seeded=True),
    ]
    (row,) = suspended_rows(AppliedPolicy(records, []), [review()])
    assert row["email"] == "c@example.org" and row["ministry_name"] == "Choir"
    assert row["member_duid"] == 3 and row["generation"] == 7
    assert str(row["reason"]).startswith("The parish source no longer shows")
    assert row["granted"] == [] and row["leading"] is False
    assert row["manual"] is False and row["last_login"] is None


def test_a_manual_assignment_beside_the_seed_keeps_scope_and_forbids_restore():
    """Only removal is offered when an Administrator's assignment already exists."""
    records = [
        address(),
        address("c@example.org", ("ministry_leader",), seeded=True),
        assignment("c@example.org", ministry=4, seeded=True),
        assignment("c@example.org", ministry=4),
    ]
    (row,) = suspended_rows(
        AppliedPolicy(records, []), [review(reason="ministry_inactive")]
    )
    assert row["manual"] is True and row["leading"] is True
    assert text(row["granted"]) == ["Ministry leader"]
    assert str(row["reason"]) == "The Ministry is inactive in the applied activity."


def test_rows_order_by_suspension_time_then_address_and_tolerate_no_name():
    """Ordering is stable and a Ministry that left the catalog shows an empty name."""
    records = [
        address(),
        address("b@example.org", ("ministry_leader",), seeded=True),
        assignment("b@example.org", ministry=9, seeded=True),
        address("c@example.org", ("ministry_leader",), seeded=True),
        assignment("c@example.org", ministry=4, seeded=True),
    ]
    rows = suspended_rows(
        AppliedPolicy(records, []),
        [
            review(),
            review(
                email="b@example.org",
                ministry_duid=9,
                ministry_name=None,
                opened_at=OPENED,
            ),
        ],
    )
    assert [(row["email"], row["ministry_name"]) for row in rows] == [
        ("b@example.org", ""),
        ("c@example.org", "Choir"),
    ]
