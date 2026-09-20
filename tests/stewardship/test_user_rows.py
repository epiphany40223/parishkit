"""Database-free review rows for login rules, provenance and assignments."""

from datetime import UTC, datetime

from parishkit.stewardship.accounts.user_rows import (
    address_rows,
    domain_assignment_rows,
    domain_rows,
)

from .policy_factory import address, assignment, domain

EARLIER = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 19, 15, 4, tzinfo=UTC)


def signin(email, *, hosted=None, at=LATER, disabled=False):
    """One mutable Google identity as the view reads it."""
    return dict(email=email, hosted_domain=hosted, verified_at=at, disabled=disabled)


def text(values):
    """Lazy translations compare as the text an Administrator reads."""
    return [str(value) for value in values]


def test_domain_rows_are_sorted_and_need_a_real_hosted_claim():
    """A matching email suffix is not evidence that a domain rule is usable."""
    records = [
        domain("zeta.example", roles=("staff", "ministry_leader")),
        domain("alpha.example", roles=()),
        address("person@alpha.example", roles=("staff",)),
    ]
    users = [
        signin("one@zeta.example", hosted="zeta.example", at=EARLIER),
        signin("two@zeta.example", hosted="zeta.example"),
        # The suffix matches, but Google presented no hosted-domain claim.
        signin("three@alpha.example"),
    ]
    alpha, zeta = domain_rows(records, users)
    assert (alpha["domain"], zeta["domain"]) == ("alpha.example", "zeta.example")
    assert text(zeta["roles"]) == ["Staff", "Ministry leader"]
    assert zeta["claims"] == 2 and zeta["last_login"] == LATER
    assert zeta["warnings"] == []
    assert alpha["claims"] == 0 and alpha["last_login"] is None
    assert text(alpha["warnings"]) == [
        "This rule grants no role.",
        "No sign-in has presented this Google hosted-domain claim yet.",
    ]


def test_address_rows_show_effective_roles_provenance_and_denial():
    """Effective roles come from the evaluator a sign-in itself uses."""
    records = [
        address("zed@example.org", roles=("staff",)),
        address("admin@example.org"),
        address("blocked@example.org", roles=()),
        domain("example.org"),
    ]
    admin, blocked, zed = address_rows(records, [signin("ADMIN@example.org")])
    assert [row["email"] for row in (admin, blocked, zed)] == [
        "admin@example.org",
        "blocked@example.org",
        "zed@example.org",
    ]
    assert text(admin["roles"]) == ["Administrator"]
    # Administrator includes the other roles; an Administrator needs no Ministry.
    assert text(admin["effective"]) == ["Administrator", "Staff", "Ministry leader"]
    assert admin["last_login"] == LATER and not admin["deny"]
    assert [text(grant["origins"]) for grant in admin["grants"]] == [
        ["Added by an Administrator"]
    ]
    assert blocked["deny"] and blocked["roles"] == [] and blocked["effective"] == []
    assert zed["last_login"] is None
    # Every one of these addresses replaces the example.org domain rule.
    assert all(
        "This exact address replaces its domain rule for this person."
        in text(row["warnings"])
        for row in (admin, blocked, zed)
    )
    assert "Ministry leader with no active Ministry assignment." not in text(
        admin["warnings"]
    )


def test_seeded_leader_is_suspended_until_the_source_confirms_a_chair():
    """A Chairperson-only role follows the promoted source, and says so."""
    rule = address("chair@example.org", roles=("ministry_leader",), seeded=True)
    held = assignment("chair@example.org", ministry=9, seeded=True)
    manual = address("leader@example.org", roles=("ministry_leader",))
    idle = assignment("staff@example.org", ministry=4)
    staff = address("staff@example.org", roles=("staff",))
    records = [rule, held, manual, idle, staff]

    chair, leader, other = address_rows(records, [])
    assert chair["effective"] == []
    assert chair["assignments"][0]["active"] is False
    assert text(chair["warnings"])[0].startswith(
        "The Ministry leader role is suspended"
    )
    assert text(chair["grants"][0]["origins"]) == ["Chairperson"]
    assert text([chair["origin"]]) == ["Chairperson"]
    assert text(leader["warnings"]) == [
        "Ministry leader with no active Ministry assignment."
    ]
    assert text(other["warnings"]) == [
        "Ministry assignments have no effect without the Ministry leader role."
    ]

    confirmed, *_ = address_rows(records, [], frozenset({held["id"]}))
    assert text(confirmed["effective"]) == ["Ministry leader"]
    assert confirmed["assignments"] == [
        {
            "ministry_duid": 9,
            "source": chair["assignments"][0]["source"],
            "active": True,
        }
    ]
    assert confirmed["warnings"] == []

    disabled, *_ = address_rows(
        records, [signin("chair@example.org", disabled=True)], frozenset({held["id"]})
    )
    assert text(disabled["warnings"]) == ["This Google identity is disabled."]


def test_assignments_relying_on_a_domain_rule_are_listed_separately():
    """Without an exact rule, only a leading domain rule can make one effective."""
    records = [
        domain("lead.example", roles=("ministry_leader",)),
        domain("staff.example", roles=("staff",)),
        assignment("b@lead.example", ministry=7),
        assignment("b@lead.example", ministry=3),
        assignment("a@staff.example", ministry=5),
        address("exact@lead.example", roles=("ministry_leader",)),
        assignment("exact@lead.example", ministry=2),
    ]
    inert, effective = domain_assignment_rows(records)
    assert (inert["email"], effective["email"]) == ("a@staff.example", "b@lead.example")
    assert [item["ministry_duid"] for item in effective["assignments"]] == [3, 7]
    assert effective["warnings"] == []
    assert text(inert["warnings"]) == [
        "No login rule gives this person the Ministry leader role."
    ]
    assert domain_assignment_rows([address()]) == []
