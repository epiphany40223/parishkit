"""Fresh explicit policy provenance for pure and PostgreSQL scenarios."""

from uuid import uuid4


def address(email="admin@example.org", roles=("administrator",), *, seeded=False):
    """Create one exact-address rule without deriving origin from current roles."""
    origin = "chair-seed" if seeded else "manual"
    return {
        "id": str(uuid4()),
        "values": {
            "kind": "address",
            "email": email,
            "roles": sorted(roles),
            "creation_origin": origin,
            "creation_operation": str(uuid4()),
            "grants": {role: {origin: str(uuid4())} for role in roles},
        },
    }


def domain(name="example.org", roles=("staff",)):
    """Hosted-domain grants must never imply Administrator."""
    return {
        "id": str(uuid4()),
        "values": {"kind": "domain", "domain": name, "roles": sorted(roles)},
    }


def assignment(email="leader@example.org", ministry=123, *, seeded=False):
    """Configured assignments need explicit source and operation identities."""
    return {
        "id": str(uuid4()),
        "values": {
            "kind": "assignment",
            "email": email,
            "ministry_duid": ministry,
            "source": "chair-seed" if seeded else "manual",
            "operation_id": str(uuid4()),
        },
    }
