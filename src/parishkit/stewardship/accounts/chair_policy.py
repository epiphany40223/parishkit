"""Pure seeded-scope decisions; neither a suggestion nor a match creates a grant."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from .ministry_activity import active_ministries


@dataclass(frozen=True)
class SeedIdentity:
    """Member explicitly selected by the Admin, never inferred from an email."""

    organization_id: int
    member_duid: int


@dataclass(frozen=True)
class ChairRelationship:
    """One current source relationship, excluding unnecessary census fields."""

    organization_id: int
    member_duid: int
    ministry_duid: int
    email: str = field(repr=False)


@dataclass(frozen=True)
class SeedDecision:
    """Proposed overlay for one existing configured seed; not an authorization."""

    assignment_record_id: UUID
    active: bool
    reason: str


def seed_decisions(document, *, organization_id, relationships, identities):
    """Reevaluate configured seeds against retained identity and current truth.

    Callers bind the verified applied document, source tenant and identities to
    the same owning transaction. Manual assignments are deliberately absent
    from the result. A missing binding fails closed rather than selecting the
    first matching email. Role suppression remains the policy resolver's exact
    provenance predicate; this calculator never rewrites roles or YAML.
    """
    if not isinstance(relationships, frozenset):
        raise TypeError("Chair relationships must be an explicit frozen set.")
    if not isinstance(identities, Mapping) or any(
        not isinstance(key, UUID) or not isinstance(value, SeedIdentity)
        for key, value in identities.items()
    ):
        raise TypeError("Chair identities require UUID keys and explicit bindings.")
    catalog = frozenset(
        row.ministry_duid
        for row in relationships
        if row.organization_id == organization_id
    )
    active = active_ministries(
        document, organization_id=organization_id, catalog_duids=catalog
    )
    inactive = {
        row["values"]["ministry_duid"]
        for row in document["sections"].get("ministries", [])
        if row["values"]["organization_id"] == organization_id
        and not row["values"]["active"]
    }
    decisions = []
    for record in document["sections"].get("login_rules", []):
        values = record["values"]
        if values.get("kind") != "assignment" or values["source"] != "chair-seed":
            continue
        identifier = UUID(record["id"])
        identity = identities.get(identifier)
        if identity is None:
            reason = "missing_binding"
        elif identity.organization_id != organization_id:
            reason = "organization_changed"
        elif values["ministry_duid"] in inactive:
            reason = "ministry_inactive"
        elif (
            values["ministry_duid"] in active
            and ChairRelationship(
                organization_id,
                identity.member_duid,
                values["ministry_duid"],
                values["email"],
            )
            in relationships
        ):
            reason = "current_chair"
        else:
            reason = "relationship_missing"
        decisions.append(SeedDecision(identifier, reason == "current_chair", reason))
    return tuple(sorted(decisions, key=lambda row: row.assignment_record_id))
