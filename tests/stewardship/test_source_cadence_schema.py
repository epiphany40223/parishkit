"""Nightly source time is public versioned policy, never authentication scope."""

from copy import deepcopy
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_schema import (
    schema_for,
    validator_for,
)
from parishkit.stewardship.accounts.integration_forms import IntegrationForm
from parishkit.stewardship.accounts.integration_selection import (
    authentication_scope,
    integration_records,
)
from parishkit.stewardship.accounts.request_patch import (
    build_candidate,
    credential_request_schema,
    default_schema,
)
from parishkit.stewardship.accounts.source_cadence_schema import (
    CREDENTIAL_SCHEMA,
    RECOVERY_SCHEMA,
    REQUEST_SCHEMA,
    SCHEMA,
)

from .configuration_factory import configuration_document, configuration_version
from .policy_factory import address


def base_version():
    """Start with an actual earlier policy schema, before nightly settings exist."""
    document = configuration_document()
    document["sections"]["login_rules"] = [address()]
    return configuration_version(document)


def cadence_patch(base, value):
    """Replace complete public settings while preserving organization and secrets."""
    record = base.document()["sections"]["integrations"][0]
    return [
        {
            "section": "integrations",
            "operation": "update",
            "id": record["id"],
            "values": {"settings": {"organization_id": "12345", "nightly_time": value}},
        }
    ]


@pytest.mark.parametrize("value", ["00:00", "02:00", "12:30", "23:59"])
def test_nightly_time_upgrade_preserves_base_and_credential_scope(value):
    """The new parser is explicit; old retained parsers continue rejecting it."""
    base = base_version()
    original = deepcopy(base.document())
    patch = cadence_patch(base, value)
    assert default_schema(base, patch) == REQUEST_SCHEMA
    candidate = build_candidate(base, patch, candidate_id=uuid4()).candidate
    assert base.document() == original
    assert schema_for(candidate.document()) == SCHEMA
    assert authentication_scope(
        "parishsoft", integration_records(candidate.document())
    ) == {
        "organization_id": 12345,
    }
    for old in ("foundation-policy-v2", "campaign-content-v5"):
        with pytest.raises(ConfigError):
            validator_for(old)(candidate.document())
    form = IntegrationForm(
        "parishsoft",
        {"base_digest": base.digest, "organization_id": "12345", "nightly_time": value},
    )
    assert form.is_valid(), form.errors
    assert form.public_settings()["nightly_time"] == value


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        2,
        {},
        "",
        "2:00",
        "24:00",
        "12:60",
        "02:00:00",
        " 02:00",
        "０２:００",
        "02:00Z",
    ],
)
def test_closed_local_time_shape_fails_before_persistence(value):
    """No timezone suffix, seconds, coercion or private detail enters the schema."""
    base = base_version()
    with pytest.raises(ConfigError):
        build_candidate(base, cadence_patch(base, value), candidate_id=uuid4())


def test_new_discriminators_retain_fingerprint_and_recovery_boundaries():
    """Cadence does not make general patches able to select credentials or grants."""
    base = base_version()
    base = build_candidate(
        base, cadence_patch(base, "03:15"), candidate_id=uuid4()
    ).candidate
    patch = cadence_patch(base, "04:00")
    patch[0]["values"] = {"credential_fingerprint": "b" * 64}
    assert credential_request_schema(base.document()) == CREDENTIAL_SCHEMA
    with pytest.raises(ConfigError):
        build_candidate(base, patch, candidate_id=uuid4())
    changed = build_candidate(
        base, patch, candidate_id=uuid4(), request_schema=CREDENTIAL_SCHEMA
    )
    assert (
        changed.candidate.document()["sections"]["integrations"][0]["values"][
            "settings"
        ]["nightly_time"]
        == "03:15"
    )
    with pytest.raises(ConfigError):
        build_candidate(
            base,
            patch,
            candidate_id=uuid4(),
            request_schema="integration-credential-patch-v6",
        )
    with pytest.raises(ConfigError):
        build_candidate(
            base,
            cadence_patch(base, "04:00"),
            candidate_id=uuid4(),
            request_schema=RECOVERY_SCHEMA,
        )
    rule = address("recovered@example.org")
    rule["values"]["grants"]["administrator"]["manual"] = rule["values"][
        "creation_operation"
    ]
    recovery = [{"section": "login_rules", "operation": "add", **rule}]
    restored = build_candidate(
        base, recovery, candidate_id=uuid4(), request_schema=RECOVERY_SCHEMA
    )
    assert schema_for(restored.candidate.document()) == SCHEMA
