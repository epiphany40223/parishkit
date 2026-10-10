"""Database-free review rows for login rules and provenance."""

from datetime import UTC, datetime

from parishkit.stewardship.accounts.user_rows import (
    AppliedPolicy,
    address_rows,
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


def test_rule_leader_roles_are_held_and_assignments_are_not_shown():
    """A rule's Ministry leader role, seeded or manual, is held: a rule is
    what admits a Ministry leader (2026-10-10), and ParishSoft decides their
    Ministries (#922). Ministry assignment records are neither shown nor
    counted, and nothing warns about them.
    """
    rule = address("chair@example.org", roles=("ministry_leader",), seeded=True)
    held = assignment("chair@example.org", ministry=9, seeded=True)
    manual = address("leader@example.org", roles=("ministry_leader",))
    idle = assignment("staff@example.org", ministry=4)
    staff = address("staff@example.org", roles=("staff",))
    chair, leader, other = address_rows(
        AppliedPolicy([rule, held, manual, idle, staff], [])
    )
    assert text(chair["granted"]) == text(leader["granted"]) == ["Ministry leader"]
    assert text(other["granted"]) == ["Staff"]
    for row in (chair, leader, other):
        assert row["warnings"] == [] and "assignments" not in row
    # Provenance is still shown as recorded.
    assert text(chair["grants"][0]["origins"]) == ["Parish source Chairperson"]
    assert text([chair["origin"]]) == ["Parish source Chairperson"]
    (admin,) = address_rows(AppliedPolicy([address()], []))
    assert admin["warnings"] == []


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
