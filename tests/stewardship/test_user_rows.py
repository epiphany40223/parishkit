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


# What every retired Ministry assignment says (#922).
RETIRED = (
    "Ministry assignments no longer grant anything: a Ministry leader sees "
    "the Ministries of their ParishSoft Ministry roles."
)


def test_rule_leader_roles_admit_and_assignments_say_they_grant_nothing():
    """A rule's role is held; assignments grant no Ministry (#922), and say so.

    A rule's Ministry leader role, seeded or manual, is granted, since a rule
    is what admits a Ministry leader (2026-10-10). No assignment is in force,
    whatever the promoted source confirms, and each row holding one says so.
    """
    rule = address("chair@example.org", roles=("ministry_leader",), seeded=True)
    held = assignment("chair@example.org", ministry=9, seeded=True)
    manual = address("leader@example.org", roles=("ministry_leader",))
    idle = assignment("staff@example.org", ministry=4)
    staff = address("staff@example.org", roles=("staff",))
    records = [rule, held, manual, idle, staff]
    for policy in (
        AppliedPolicy(records, []),
        AppliedPolicy(records, [], frozenset({held["id"]})),
    ):
        chair, leader, other = address_rows(policy)
        assert text(chair["granted"]) == text(leader["granted"]) == ["Ministry leader"]
        assert text(other["granted"]) == ["Staff"]
        assert [item["active"] for item in chair["assignments"]] == [False]
        for row in (chair, other):
            assert text(row["warnings"]) == [RETIRED]
        assert leader["warnings"] == []
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


def test_assignments_relying_on_a_domain_rule_lead_nothing():
    """No assignment leads now (#922), whatever rule or claim backs it."""
    held = assignment("chair@lead.example", ministry=9, seeded=True)
    records = [
        address(),
        domain("lead.example", roles=("ministry_leader",)),
        assignment("claimed@lead.example", ministry=7),
        assignment("claimed@lead.example", ministry=3),
        assignment("off@lead.example", ministry=8),
        address("exact@lead.example", roles=("ministry_leader",)),
        assignment("exact@lead.example", ministry=1),
        held,
    ]
    identities = [
        identity("claimed@lead.example", hosted="lead.example"),
        identity("off@lead.example", hosted="lead.example", disabled=True),
        identity("chair@lead.example", hosted="lead.example"),
    ]
    rows = {
        row["email"]: row
        for row in domain_assignment_rows(
            AppliedPolicy(records, identities, frozenset({held["id"]}))
        )
    }
    assert list(rows) == sorted(rows) and "exact@lead.example" not in rows
    claimed = rows["claimed@lead.example"]
    assert [item["ministry_duid"] for item in claimed["assignments"]] == [3, 7]
    assert claimed["last_login"] == LATER
    for row in rows.values():
        assert not row["leading"]
        assert not any(item["active"] for item in row["assignments"])
        assert text(row["warnings"])[0] == RETIRED
    # A disabled identity still says so too.
    assert len(rows["off@lead.example"]["warnings"]) == 2
    assert domain_assignment_rows(AppliedPolicy([address()], [])) == []
