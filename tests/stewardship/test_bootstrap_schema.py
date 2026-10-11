"""Initial login authority contains no invented parish/wizard configuration."""

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authority import parse_version
from parishkit.stewardship.accounts.bootstrap_schema import bootstrap_version
from parishkit.stewardship.accounts.configuration_schema import (
    schema_for,
    validate_sections,
    validator_for,
)

from .policy_factory import address, domain, legacy_bootstrap_document, user


def test_minimal_deterministic_authority():
    """Explicit deployment identity and normalized address bind repeat provisioning."""
    deployment = uuid4()
    version = bootstrap_version(deployment, "Admin@Example.org")
    assert version == bootstrap_version(deployment, "admin@example.org")
    assert version != bootstrap_version(uuid4(), "admin@example.org")
    assert version != bootstrap_version(deployment, "other@example.org")
    document = version.document()
    assert set(document["sections"]) == {"login_rules"}
    assert schema_for(document) == "bootstrap-users-v10"
    assert parse_version(document, validate_sections=validate_sections) == version
    with pytest.raises(ConfigError):
        bootstrap_version(str(deployment), "admin@example.org")


def test_bootstrap_writes_one_administrator_user():
    """The initial Administrator is a user (#993), named by the local part."""
    [record] = bootstrap_version(uuid4(), "Pat.Smith@Example.org").document()[
        "sections"
    ]["login_rules"]
    assert record["values"] == {
        "kind": "user",
        "email": "pat.smith@example.org",
        "name": "pat.smith",
        "role": "administrator",
    }


@pytest.mark.parametrize(
    "schema",
    ["parish-integrations-v1", "foundation-policy-v2", "campaign-foundation-v3"],
)
def test_historical_schemas_do_not_gain_bootstrap_loophole(schema):
    """Retained historical validators still require their complete parish profile."""
    with pytest.raises(ConfigError):
        parse_version(
            bootstrap_version(uuid4(), "admin@example.org").document(),
            validate_sections=validator_for(schema),
        )


def test_users_validator_needs_the_complete_configuration():
    """A users bootstrap is not a users-v10 configuration: it has no Parish."""
    with pytest.raises(ConfigError):
        parse_version(
            bootstrap_version(uuid4(), "admin@example.org").document(),
            validate_sections=validator_for("users-v10"),
        )


@pytest.mark.parametrize(
    "records",
    [
        [],
        [address(), address("other@example.org")],
        [domain()],
        [address(roles=("administrator", "staff"))],
        [user(), user("other@example.org")],
        [user(role="staff")],
        [user(role="ministry_leader")],
        [user(name=" padded ")],
        [user() | {"values": user()["values"] | {"extra": True}}],
        [user(), address("other@example.org")],
    ],
)
def test_root_excludes_empty_multiple_and_nonminimal_grants(records):
    """The root permits exactly one manually provisioned Administrator."""
    document = bootstrap_version(uuid4(), "admin@example.org").document()
    document["sections"]["login_rules"] = records
    with pytest.raises(ConfigError):
        parse_version(document, validate_sections=validate_sections)


def test_bootstrap_successor_can_hold_multiple_recovery_admins():
    """Recovery preserves existing Admins rather than replacing them."""
    root = bootstrap_version(uuid4(), "admin@example.org")
    document = root.document()
    document.update(version_id=str(uuid4()), predecessor_digest=root.digest)
    document["sections"]["login_rules"].append(user("new@example.org"))
    validate_sections(document)
    assert schema_for(document) == "bootstrap-users-v10"
    # Recovery adds only Administrators, and each address once.
    for extra in (user("staff@example.org", "staff"), user("ADMIN@example.org")):
        document["sections"]["login_rules"][-1] = extra
        with pytest.raises(ConfigError):
            validate_sections(document)


def test_an_older_exact_address_bootstrap_still_validates():
    """A deployment bootstrapped before #993 keeps its exact-address form."""
    document = legacy_bootstrap_document()
    assert schema_for(document) == "bootstrap-policy-v1"
    root = parse_version(document, validate_sections=validate_sections)
    successor = root.document()
    successor.update(version_id=str(uuid4()), predecessor_digest=root.digest)
    successor["sections"]["login_rules"].append(address("new@example.org"))
    validate_sections(successor)
