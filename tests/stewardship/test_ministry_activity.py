"""Local activity validation, frozen schema replay and tenant-scoped selection."""

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authority import parse_version
from parishkit.stewardship.accounts.configuration_schema import (
    schema_for,
    validator_for,
)
from parishkit.stewardship.accounts.ministry_activity import (
    RECOVERY_SCHEMA,
    REQUEST_SCHEMA,
    SCHEMA,
    active_ministries,
)
from parishkit.stewardship.accounts.request_patch import build_candidate, default_schema

from .configuration_factory import configuration_document, configuration_version
from .policy_factory import address, assignment


def activity(*, organization_id=12345, ministry_duid=1, active=False):
    """Fresh explicit source identity, with no display-name binding."""
    return {
        "id": str(uuid4()),
        "values": {
            "organization_id": organization_id,
            "ministry_duid": ministry_duid,
            "active": active,
        },
    }


def policy_document(*records):
    """Complete synthetic authority; Ministry overrides require configured login."""
    document = configuration_document()
    document["sections"]["login_rules"] = [address()]
    document["sections"]["ministries"] = list(records)
    return document


@pytest.mark.parametrize("field", ["organization_id", "ministry_duid"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 2**31, "1", None, 1.0])
def test_reject_inexact_source_identity(field, value):
    """Boolean, coercible and out-of-range IDs must never name another Ministry."""
    row = activity()
    row["values"][field] = value
    with pytest.raises(ConfigError):
        configuration_version(policy_document(row))


@pytest.mark.parametrize("value", [0, 1, "true", "false", None, [], {}])
def test_activity_is_explicit_boolean(value):
    """Missing and truthy strings are not validated activation decisions."""
    with pytest.raises(ConfigError):
        configuration_version(policy_document(activity(active=value)))


@pytest.mark.parametrize("defect", ["missing", "extra", "duplicate"])
def test_exact_record_shape_and_unique_identity(defect):
    """Only a single explicit decision can exist for each tenant/Ministry pair."""
    row = activity()
    document = policy_document(row)
    if defect == "missing":
        del row["values"]["active"]
    elif defect == "extra":
        row["values"]["name"] = "Not an identity"
    else:
        document["sections"]["ministries"].append(activity())
    with pytest.raises(ConfigError):
        configuration_version(document)


@pytest.mark.parametrize(
    "old_schema",
    ["parish-integrations-v1", "foundation-policy-v2", "campaign-foundation-v3"],
)
def test_old_schema_never_reinterprets_activity(old_schema):
    """Retained retry/history parsers keep their previous accepted vocabulary."""
    document = policy_document(activity())
    assert schema_for(document) == SCHEMA
    with pytest.raises(ConfigError):
        parse_version(document, validate_sections=validator_for(old_schema))


def test_selection_is_local_tenant_specific_and_requires_catalog_presence():
    """Source absence wins; unconfigured entries default active across refreshes."""
    document = policy_document(
        activity(ministry_duid=1),
        activity(ministry_duid=2, organization_id=67890),
        activity(ministry_duid=3, active=True),
        activity(ministry_duid=9, active=True),
    )
    document = configuration_version(document).document()
    assert active_ministries(
        document, organization_id=12345, catalog_duids=frozenset({1, 2, 3, 4})
    ) == frozenset({2, 3, 4})
    assert active_ministries(
        document, organization_id=12345, catalog_duids=frozenset({2})
    ) == frozenset({2})
    assert active_ministries(
        document, organization_id=12345, catalog_duids=frozenset({1, 2})
    ) == frozenset({2})


@pytest.mark.parametrize(
    "organization,catalog",
    [
        (True, frozenset({1})),
        ("12345", frozenset({1})),
        (12345, {1}),
        (12345, frozenset({True})),
        (12345, frozenset({2**31})),
    ],
)
def test_selection_rejects_untyped_identity_inputs(organization, catalog):
    """A convenience selector cannot silently coerce an untrusted source identity."""
    with pytest.raises(ValueError):
        active_ministries(
            policy_document(), organization_id=organization, catalog_duids=catalog
        )


def test_activity_patch_add_edit_remove_replay_and_keep_unrelated_content():
    """New patches use v4; removal and frozen retry give identical candidates."""
    base = configuration_version(policy_document())
    row = activity()
    patch = [{"operation": "add", "section": "ministries", **row}]
    assert default_schema(base, patch) == REQUEST_SCHEMA
    created = build_candidate(base, patch, candidate_id=uuid4()).candidate
    edit = [
        {
            "operation": "update",
            "section": "ministries",
            "id": row["id"],
            "values": {"active": True},
        }
    ]
    updated = build_candidate(created, edit, candidate_id=uuid4()).candidate
    assert updated.document()["sections"]["ministries"][0]["values"]["active"] is True
    assert (
        updated.document()["sections"]["login_rules"]
        == base.document()["sections"]["login_rules"]
    )
    remove = [{"operation": "remove", "section": "ministries", "id": row["id"]}]
    candidate_id = uuid4()
    removed = build_candidate(updated, remove, candidate_id=candidate_id)
    assert removed == build_candidate(
        updated, remove, candidate_id=candidate_id, request_schema=REQUEST_SCHEMA
    )
    assert schema_for(removed.candidate.document()) == "foundation-policy-v2"


@pytest.mark.parametrize("field", ["organization_id", "ministry_duid"])
def test_patch_cannot_rebind_record(field):
    """Changing activity is supported; moving its stable record to another ID is not."""
    row = activity()
    base = configuration_version(policy_document(row))
    with pytest.raises(ConfigError, match="stable"):
        build_candidate(
            base,
            [
                {
                    "operation": "update",
                    "section": "ministries",
                    "id": row["id"],
                    "values": {field: 65432},
                }
            ],
            candidate_id=uuid4(),
        )


def test_recovery_preserves_ministry_policy_and_only_adds_admin():
    """Operator recovery cannot disguise an activity edit as account repair."""
    base = configuration_version(policy_document(activity()))
    admin = address("recovery@example.org")
    admin["values"]["grants"]["administrator"]["manual"] = admin["values"][
        "creation_operation"
    ]
    result = build_candidate(
        base,
        [{"operation": "add", "section": "login_rules", **admin}],
        candidate_id=uuid4(),
        request_schema=RECOVERY_SCHEMA,
    )
    assert (
        result.candidate.document()["sections"]["ministries"]
        == base.document()["sections"]["ministries"]
    )
    with pytest.raises(ConfigError):
        build_candidate(
            base,
            [
                {
                    "operation": "add",
                    "section": "ministries",
                    **activity(ministry_duid=2),
                }
            ],
            candidate_id=uuid4(),
            request_schema=RECOVERY_SCHEMA,
        )


def test_activity_schema_does_not_enable_unconfirmed_seed_grants():
    """The new format retains the manual-policy provenance boundary."""
    base = configuration_version(policy_document(activity()))
    with pytest.raises(ConfigError):
        build_candidate(
            base,
            [{"operation": "add", "section": "login_rules", **assignment(seeded=True)}],
            candidate_id=uuid4(),
        )
