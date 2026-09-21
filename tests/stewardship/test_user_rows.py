"""Database-free review rows for login rules, provenance and assignments."""

from datetime import UTC, datetime

from parishkit.stewardship.accounts.user_rows import (
    AppliedPolicy,
    address_rows,
    domain_assignment_rows,
    domain_rows,
)

from .policy_factory import address, assignment, domain

EARLIER = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 19, 15, 4, tzinfo=UTC)


def identity(email, *, hosted=None, login=LATER, disabled=False):
    """One recorded Google identity; `login` is its last *successful* sign-in."""
    return dict(email=email, hosted_domain=hosted, last_login=login, disabled=disabled)


def text(values):
    """Lazy translations compare as the text an Administrator reads."""
    return [str(value) for value in values]


def test_domain_rows_count_only_accounts_the_rule_really_authorizes():
    """Neither a matching suffix nor a bare hosted claim is evidence by itself."""
    records = [
        address(),
        domain("zeta.example", roles=("staff", "ministry_leader")),
        domain("alpha.example", roles=("staff",)),
        address("denied@zeta.example", roles=()),
    ]
    identities = [
        identity("one@zeta.example", hosted="zeta.example", login=EARLIER),
        identity("two@zeta.example", hosted="zeta.example"),
        # An exact rule, here an explicit deny, replaces the domain rule.
        identity("denied@zeta.example", hosted="zeta.example", login=None),
        identity("off@zeta.example", hosted="zeta.example", disabled=True),
        # A Workspace alias: the primary claim with another email suffix.
        identity("alias@other.example", hosted="zeta.example"),
        # The suffix matches, but Google presented no hosted-domain claim.
        identity("four@alpha.example"),
    ]
    alpha, zeta = domain_rows(AppliedPolicy(records, identities))
    assert (alpha["domain"], zeta["domain"]) == ("alpha.example", "zeta.example")
    assert text(zeta["roles"]) == ["Staff", "Ministry leader"]
    assert zeta["authorized"] == 2 and zeta["last_login"] == LATER
    assert zeta["warnings"] == []
    # Nobody is authorized through alpha's rule, yet the last successful sign-in
    # is, as for an address, over every recorded identity at that domain.
    assert alpha["authorized"] == 0 and alpha["last_login"] == LATER
    assert len(alpha["warnings"]) == 1
    assert text(alpha["warnings"])[0].startswith(
        "No recorded Google account is authorized through this rule yet."
    )


def test_address_rows_show_granted_roles_provenance_and_denial():
    """Granted roles come from the evaluator a sign-in itself uses."""
    records = [
        address("zed@example.org", roles=("staff",)),
        address("admin@example.org"),
        address("blocked@example.org", roles=()),
        domain("example.org"),
    ]
    identities = [
        identity("ADMIN@example.org"),
        # Google verified this attempt, policy refused it: not a sign-in.
        identity("blocked@example.org", login=None),
    ]
    admin, blocked, zed = address_rows(AppliedPolicy(records, identities))
    assert [row["email"] for row in (admin, blocked, zed)] == [
        "admin@example.org",
        "blocked@example.org",
        "zed@example.org",
    ]
    assert text(grant["role"] for grant in admin["grants"]) == ["Administrator"]
    # Administrator includes the other roles; an Administrator needs no Ministry.
    assert text(admin["granted"]) == ["Administrator", "Staff", "Ministry leader"]
    assert admin["last_login"] == LATER and not admin["deny"]
    assert [text(grant["origins"]) for grant in admin["grants"]] == [
        ["Administrator entry"]
    ]
    assert blocked["deny"] and blocked["grants"] == [] and blocked["granted"] == []
    # An explicit deny never shows a sign-in merely because someone tried.
    assert blocked["last_login"] is None and zed["last_login"] is None
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

    chair, leader, other = address_rows(AppliedPolicy(records, []))
    assert chair["granted"] == []
    assert chair["assignments"][0]["active"] is False
    # The whole list: a suspended role is not told to go and get the role.
    assert text(chair["warnings"]) == [
        "The Ministry leader role is suspended: no Chairperson assignment is "
        "currently confirmed by the parish source."
    ]
    assert text(chair["grants"][0]["origins"]) == ["Parish source Chairperson"]
    assert text([chair["origin"]]) == ["Parish source Chairperson"]
    assert text(leader["warnings"]) == [
        "Ministry leader with no active Ministry assignment."
    ]
    assert text(other["warnings"]) == [
        "Ministry assignments have no effect without the Ministry leader role."
    ]

    confirmed, *_ = address_rows(AppliedPolicy(records, [], frozenset({held["id"]})))
    assert text(confirmed["granted"]) == ["Ministry leader"]
    assert [item["active"] for item in confirmed["assignments"]] == [True]
    assert confirmed["warnings"] == []


def test_several_google_identities_for_one_address_are_grouped():
    """Google's subject owns identity, so one address may have more than one."""
    records = [address(), address("moved@example.org", roles=("staff",))]
    old = identity("moved@example.org", login=EARLIER, disabled=True)
    new = identity("Moved@example.org", login=LATER)
    for identities in ([old, new], [new, old]):
        _, moved = address_rows(AppliedPolicy(records, identities))
        # The latest successful sign-in, whichever row the query returned last.
        assert moved["last_login"] == LATER
        assert text(moved["granted"]) == ["Staff"]
        assert text(moved["warnings"]) == [
            "1 of 2 recorded Google identities for this address is disabled."
        ]
    _, only = address_rows(AppliedPolicy(records, [old]))
    # Policy still grants the address; this identity simply cannot use it.
    assert text(only["granted"]) == ["Staff"]
    assert text(only["warnings"]) == [
        "The recorded Google identity for this address is disabled and cannot "
        "sign in, whatever policy grants the address."
    ]
    _, both = address_rows(AppliedPolicy(records, [old, old | {"last_login": None}]))
    assert text(both["warnings"])[0].startswith("All 2 recorded Google identities")


def test_assignments_relying_on_a_domain_rule_use_the_real_hosted_claim():
    """A consumer account on the same suffix receives nothing, and the page says so."""
    records = [
        address(),
        domain("lead.example", roles=("ministry_leader",)),
        domain("staff.example", roles=("staff",)),
        assignment("claimed@lead.example", ministry=7),
        assignment("claimed@lead.example", ministry=3),
        assignment("consumer@lead.example", ministry=6),
        assignment("unseen@lead.example", ministry=5),
        assignment("off@lead.example", ministry=8),
        assignment("nobody@staff.example", ministry=2),
        address("exact@lead.example", roles=("ministry_leader",)),
        assignment("exact@lead.example", ministry=1),
    ]
    identities = [
        identity("claimed@lead.example", hosted="lead.example"),
        # The address ends with the domain, but Google presented no such claim.
        identity("consumer@lead.example", login=None),
        identity("off@lead.example", hosted="lead.example", disabled=True),
    ]
    rows = {
        row["email"]: row
        for row in domain_assignment_rows(AppliedPolicy(records, identities))
    }
    assert list(rows) == sorted(rows) and "exact@lead.example" not in rows
    claimed = rows["claimed@lead.example"]
    assert claimed["leading"] and claimed["warnings"] == []
    assert [item["ministry_duid"] for item in claimed["assignments"]] == [3, 7]
    assert claimed["last_login"] == LATER
    consumer = rows["consumer@lead.example"]
    assert not consumer["leading"]
    assert text(consumer["warnings"])[0].startswith("No usable Google identity")
    off = rows["off@lead.example"]
    assert not off["leading"] and len(off["warnings"]) == 2
    unseen = rows["unseen@lead.example"]
    assert not unseen["leading"]
    assert "lead.example hosted-domain claim" in text(unseen["warnings"])[0]
    # The root cause is stated whether or not the person has signed in.
    nobody = records[:] + [assignment("seen@staff.example", ministry=1)]
    seen = identity("seen@staff.example", hosted="staff.example")
    rows = {
        row["email"]: row
        for row in domain_assignment_rows(AppliedPolicy(nobody, [seen]))
    }
    for email in ("nobody@staff.example", "seen@staff.example"):
        assert text(rows[email]["warnings"]) == [
            "No login rule gives this person the Ministry leader role."
        ]
    assert domain_assignment_rows(AppliedPolicy([address()], [])) == []


def test_a_role_with_only_suspended_assignments_leads_nothing():
    """Valid policy can keep a seeded assignment after its exact rule is gone."""
    held = assignment("chair@lead.example", ministry=9, seeded=True)
    records = [address(), domain("lead.example", roles=("ministry_leader",)), held]
    known = [identity("chair@lead.example", hosted="lead.example")]
    (row,) = domain_assignment_rows(AppliedPolicy(records, known))
    # The domain rule grants the role, but no assignment is in force.
    assert not row["leading"] and row["assignments"][0]["active"] is False
    assert text(row["warnings"])[0].startswith("No usable Google identity")
    (confirmed,) = domain_assignment_rows(
        AppliedPolicy(records, known, frozenset({held["id"]}))
    )
    assert confirmed["leading"] and confirmed["warnings"] == []
