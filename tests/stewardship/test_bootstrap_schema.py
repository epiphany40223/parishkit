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

from .policy_factory import address, domain


def test_minimal_deterministic_authority():
    """Explicit deployment identity and normalized address bind repeat provisioning."""
    deployment = uuid4()
    version = bootstrap_version(deployment, "Admin@Example.org")
    assert version == bootstrap_version(deployment, "admin@example.org")
    assert version != bootstrap_version(uuid4(), "admin@example.org")
    assert version != bootstrap_version(deployment, "other@example.org")
    document = version.document()
    assert set(document["sections"]) == {"login_rules"}
    assert schema_for(document) == "bootstrap-policy-v1"
    assert parse_version(document, validate_sections=validate_sections) == version
    with pytest.raises(ConfigError):
        bootstrap_version(str(deployment), "admin@example.org")


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


@pytest.mark.parametrize(
    "records",
    [
        [],
        [address(), address("other@example.org")],
        [domain()],
        [address(roles=("administrator", "staff"))],
    ],
)
def test_root_excludes_empty_multiple_and_nonminimal_grants(records):
    """The root permits exactly one manually provisioned exact-address Admin."""
    document = bootstrap_version(uuid4(), "admin@example.org").document()
    document["sections"]["login_rules"] = records
    with pytest.raises(ConfigError):
        parse_version(document, validate_sections=validate_sections)


def test_bootstrap_successor_can_hold_multiple_recovery_admins():
    """Recovery preserves existing Admins rather than replacing their grants."""
    root = bootstrap_version(uuid4(), "admin@example.org")
    document = root.document()
    document.update(version_id=str(uuid4()), predecessor_digest=root.digest)
    document["sections"]["login_rules"].append(address("new@example.org"))
    validate_sections(document)
