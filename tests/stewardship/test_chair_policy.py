"""Seed suspension binds to a selected Member, not merely a matching address."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest

from parishkit.stewardship.accounts.chair_policy import (
    ChairRelationship,
    SeedIdentity,
    seed_decisions,
)

from .policy_factory import address, assignment
from .test_ministry_activity import activity, policy_document


def inputs():
    """Return verified-shape synthetic policy and retained explicit Member evidence."""
    document = policy_document()
    seed = assignment("leader@example.org", 4, seeded=True)
    document["sections"]["login_rules"] += [
        address("leader@example.org", ("ministry_leader",), seeded=True),
        seed,
        assignment("leader@example.org", 9),
    ]
    return document, {
        "organization_id": 12345,
        "relationships": frozenset(
            {ChairRelationship(12345, 3, 4, "leader@example.org")}
        ),
        "identities": {UUID(seed["id"]): SeedIdentity(12345, 3)},
    }


@pytest.mark.parametrize(
    "variant,reason",
    [
        ("normal", "current_chair"),
        ("missing_binding", "missing_binding"),
        ("different_member", "relationship_missing"),
        ("different_email", "relationship_missing"),
        ("different_ministry", "relationship_missing"),
        ("different_source_tenant", "relationship_missing"),
        ("different_configured_tenant", "organization_changed"),
        ("absent", "relationship_missing"),
        ("inactive", "ministry_inactive"),
        ("other_override_tenant", "current_chair"),
        ("shared_email", "current_chair"),
        ("manual_only", None),
    ],
)
def test_exact_member_and_source_policy_decisions(variant, reason):
    """Source truth can remove only seeded scope, never transfer or add it."""
    document, arguments = inputs()
    if variant == "missing_binding":
        arguments["identities"] = {}
    elif variant == "different_member":
        arguments["relationships"] = frozenset(
            {ChairRelationship(12345, 6, 4, "leader@example.org")}
        )
    elif variant == "different_email":
        arguments["relationships"] = frozenset(
            {ChairRelationship(12345, 3, 4, "changed@example.org")}
        )
    elif variant == "different_ministry":
        arguments["relationships"] = frozenset(
            {ChairRelationship(12345, 3, 8, "leader@example.org")}
        )
    elif variant == "different_source_tenant":
        arguments["relationships"] = frozenset(
            {ChairRelationship(98765, 3, 4, "leader@example.org")}
        )
    elif variant == "different_configured_tenant":
        arguments["organization_id"] = 98765
    elif variant == "absent":
        arguments["relationships"] = frozenset()
    elif variant in {"inactive", "other_override_tenant"}:
        document["sections"]["ministries"] = [
            activity(
                ministry_duid=4,
                organization_id=12345 if variant == "inactive" else 98765,
            )
        ]
    elif variant == "shared_email":
        arguments["relationships"] |= frozenset(
            {ChairRelationship(12345, 6, 4, "leader@example.org")}
        )
    elif variant == "manual_only":
        document["sections"]["login_rules"] = [address(), assignment()]
    before = deepcopy((document, arguments))
    result = seed_decisions(document, **arguments)
    assert (document, arguments) == before
    if reason is None:
        assert not result
    else:
        (decision,) = result
        assert decision.reason == reason
        assert decision.active == (reason == "current_chair")


def test_source_return_reactivates_only_the_original_relationship():
    """A suspended seed can recover without rewriting its retained identity."""
    document, arguments = inputs()
    original = seed_decisions(document, **arguments)
    assert original[0].active
    assert not seed_decisions(document, **dict(arguments, relationships=frozenset()))[
        0
    ].active
    assert seed_decisions(document, **arguments) == original


def test_relationships_must_be_explicit_and_private_values_are_not_represented():
    """Accidental lazy or omitted evidence is not treated as an empty source set."""
    document, arguments = inputs()
    assert "leader@example.org" not in repr(arguments["relationships"])
    with pytest.raises(TypeError, match="explicit frozen"):
        seed_decisions(document, **dict(arguments, relationships=None))


@pytest.mark.parametrize(
    "identities", [None, [], {"not-a-uuid": SeedIdentity(1, 2)}, {UUID(int=1): None}]
)
def test_wrong_identity_shapes_cannot_create_bulk_revocation(identities):
    """Malformed evidence is not an empty binding set; missing bindings remain valid."""
    document, arguments = inputs()
    with pytest.raises(TypeError, match="UUID keys"):
        seed_decisions(document, **dict(arguments, identities=identities))


@pytest.mark.parametrize("organization", [None, "", "wrong", "0123", "0", "2147483648"])
def test_unavailable_chair_organization_fails_before_reading_source(organization):
    """Missing or malformed scope produces a domain refusal before any source read."""
    from parishkit.stewardship.accounts.chair_reconciliation import _decisions
    from parishkit.stewardship.storage import StorageInvariantError

    sections = (
        {}
        if organization is None
        else {
            "integrations": [
                {
                    "values": {
                        "kind": "parishsoft",
                        "settings": {"organization_id": organization},
                    }
                }
            ]
        }
    )
    configuration = SimpleNamespace(canonical_document={"sections": sections})
    with pytest.raises(StorageInvariantError, match="configured ParishSoft"):
        _decisions(configuration, None)
