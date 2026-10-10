"""The users form of the sign-in policy (#952): one email, one Name, one role.

A configuration in this form uses the ``users-v10`` document schema: every
rule of ``source-cadence-v8``, except that ``login_rules`` holds only user
entries, ``{kind: "user", email, name, role}``, keyed by the record ``id``
(a stable opaque UUID). Roles are levels, each including everything below
it, so a user's projected ``roles`` is the cumulative set of its ``role``
and every SQL check that tests ``roles`` keeps its meaning. There is no
empty role, no hosted-domain rule and no grant provenance. The data
specification's "Administration user and policy" section is the contract.

The name ``users-v10`` skips v9 because ``chair-seed-patch-v9`` already uses
that suffix as a request format.
"""

from parishkit.stewardship.schema_primitives import invalid

from .policy_schema import normalized_email

SCHEMA = "users-v10"
# The one offline conversion format (#952): an operator-upgrade request.
UPGRADE_REQUEST_SCHEMA = "operator-upgrade-users-v10"
# Lowest first: each role includes every role before it.
ROLE_LEVELS = ("ministry_leader", "staff", "administrator")
NAME_LIMIT = 200


def cumulative_roles(role):
    """Return the sorted roles a user of this role holds, as the projection stores.

    ``administrator`` holds all three, ``staff`` holds Staff and Ministry
    leader, and ``ministry_leader`` holds only itself.
    """
    if role not in ROLE_LEVELS:
        invalid()
    return sorted(ROLE_LEVELS[: ROLE_LEVELS.index(role) + 1])


def record_roles(values):
    """The roles one ``login_rules`` record grants, in either policy form.

    A user grants its cumulative set; an older exact-address or domain rule
    grants its configured roles, where Administrator already implied the
    rest. Assignments grant no role.
    """
    if values["kind"] == "user":
        return cumulative_roles(values["role"])
    return list(values.get("roles", ()))


def uses_users(document):
    """True when the document's sign-in policy is already in the users form."""
    return any(
        isinstance(record, dict)
        and isinstance(record.get("values"), dict)
        and record["values"].get("kind") == "user"
        for record in document["sections"].get("login_rules", [])
    )


def valid_name(value):
    """Refuse a Name that is not 1–200 trimmed characters free of controls."""
    if (
        type(value) is not str
        or not 1 <= len(value) <= NAME_LIMIT
        or value != value.strip()
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value)
    ):
        invalid()
    return value


def validate_user_records(records):
    """Validate the users list: exact fields, unique addresses, an Administrator.

    Each address is normalized (trimmed and lowercased, with no provider
    folding) and appears once. A users-form policy always holds at least one
    Administrator: the last-Administrator guard, which SQL repeats at
    activation.
    """
    seen, administrators = set(), 0
    for record in records:
        values = record["values"]
        if set(values) != {"kind", "email", "name", "role"} or values["kind"] != "user":
            invalid()
        email = values["email"]
        # normalized_email refuses a malformed address itself.
        if normalized_email(email) != email:
            invalid()
        valid_name(values["name"])
        cumulative_roles(values["role"])
        if email in seen:
            invalid()
        seen.add(email)
        administrators += values["role"] == "administrator"
    if not administrators:
        invalid()


def validate_sections(document):
    """Validate every v8 rule, with the users list in place of the old policy."""
    from . import source_cadence_schema

    source_cadence_schema.validate_sections(document, policy=validate_user_records)
