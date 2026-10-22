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
# The request formats over a users configuration (#952 slice 3): ordinary
# Admin edits, an integration credential update, and offline Admin-access
# recovery. Each keeps every rule of its source-cadence-v8 counterpart, with
# the users list in place of the sign-in rules.
REQUEST_SCHEMA = "users-patch-v10"
CREDENTIAL_SCHEMA = "integration-credential-users-v10"
RECOVERY_SCHEMA = "operator-recovery-users-v10"
# The users form plus remembered reminder series (#469, ``series_schema``).
# The three request formats above build its candidates too, so every rule of
# the users form holds for it, and SQL treats it as the users form.
SERIES_SCHEMA = "campaign-series-v11"
# The users form plus report emails (#1786, ``reports_schema``): every rule
# of the series schema for the other records, and report schedule records
# in place of the Daily and Weekly digests. The same request formats build
# its candidates.
REPORTS_SCHEMA = "campaign-series-v12"
# The post-setup schemas in the users form.
FORM_SCHEMAS = frozenset({SCHEMA, SERIES_SCHEMA, REPORTS_SCHEMA})
# Set by the release that swaps the SQL sign-in functions and brings the
# Users page (#952 slices 4 and 5, shipped together): web, workers and
# scheduler refuse to start on a configuration of sign-in rules
# (``require_users_form``), which the offline ``upgrade-users`` converts.
USERS_ENABLED = True
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


def local_part_name(email):
    """The default Name of a user no one named: the address's local part."""
    return email.rsplit("@", 1)[0]


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


def validate_user_change(old_records, new_records):
    """Refuse an edit that moves a user to another address (#952).

    A user's ``id`` is bound to its address for good: changing the address
    is Delete and then New, so the old address's sessions and history never
    pass to someone else. Everything else about the list (one user per
    address, the shapes, and at least one Administrator) is the schema's own
    check of the whole candidate.
    """
    addresses = {record["id"]: record["values"]["email"] for record in old_records}
    for record in new_records:
        email = record["values"]["email"]
        if addresses.get(record["id"], email) != email:
            invalid()


def require_users_form(runtime, *, enabled=None):
    """Refuse to run on a configuration of sign-in rules once users ship (#952).

    The release that swaps the SQL sign-in functions reads only users, so web,
    workers and scheduler must not start before ``upgrade-users`` converted
    the configuration. A bootstrap in the users form (``bootstrap-users-v10``,
    #993) already holds only Administrator users, and its setup finishes in
    the users form. A deployment still in initial setup on an older
    exact-address bootstrap (``bootstrap-policy-v1``, written before #993) is
    exempt too, because setup cannot finish otherwise; its setup then ends in
    sign-in rules, which ``upgrade-users`` converts. Initial setup means
    exactly what ``setup_startup.initial_setup_hold`` requires: the bootstrap
    configuration, Testing mode, no current campaign and no restore review
    pending, so no deployment past setup can pass as one. The frozen
    migration 0052 judges the same exemption. ``enabled`` defaults to
    ``USERS_ENABLED``.
    """
    from parishkit.config import ConfigError

    from .bootstrap_schema import BOOTSTRAP_SCHEMA, USERS_BOOTSTRAP_SCHEMA

    if not (USERS_ENABLED if enabled is None else enabled):
        return
    schema = runtime.active_configuration.validation_schema
    in_setup = (
        schema == BOOTSTRAP_SCHEMA
        and runtime.mode == "testing"
        and runtime.current_campaign_id is None
        and not runtime.restore_review_required
    )
    if schema not in {*FORM_SCHEMAS, USERS_BOOTSTRAP_SCHEMA} and not in_setup:
        raise ConfigError(
            "The configuration still holds sign-in rules; run upgrade-users first."
        )


def validate_sections(document):
    """Validate every v8 rule, with the users list in place of the old policy."""
    from . import source_cadence_schema

    source_cadence_schema.validate_sections(document, policy=validate_user_records)
