"""Fresh explicit policy provenance for pure and PostgreSQL scenarios."""

from uuid import uuid4

from parishkit.stewardship.accounts.users_schema import uses_users


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


def user(email="admin@example.org", role="administrator", name=None):
    """One users-form record (#952): an address, a Name and one role."""
    return {
        "id": str(uuid4()),
        "values": {
            "kind": "user",
            "email": email,
            "name": email.split("@", 1)[0] if name is None else name,
            "role": role,
        },
    }


def legacy_bootstrap_document(email="admin@example.org"):
    """A root exact-address bootstrap, as bootstrap wrote one before #993."""
    return {
        "schema_version": 1,
        "version_id": str(uuid4()),
        "predecessor_digest": None,
        "sections": {"login_rules": [address(email)]},
    }


def administrator(version, email):
    """An Administrator record for ``email`` in the policy form of ``version``.

    Setup and bootstrap write users (#952), while older fixtures still hold
    sign-in rules, so a helper adding an Admin must match the base it edits.
    """
    return user(email) if uses_users(version.document()) else address(email)
