"""Credential-free request intent validation and canonical retry identity."""

from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.request_patch import build_candidate

from .configuration_factory import configuration_version


def parish_patch(base, **values):
    """Build a minimal update against one immutable synthetic parish identity."""
    return [
        {
            "section": "parish",
            "operation": "update",
            "id": base.document()["sections"]["parish"][0]["id"],
            "values": values,
        }
    ]


def test_candidate_is_canonical_and_does_not_mutate_base_or_input():
    """Retry identity excludes generated candidate IDs but includes base and intent."""
    base = configuration_version()
    patch = parish_patch(base, name="New Parish Name")
    result = build_candidate(base, patch, candidate_id=uuid4())
    retry = build_candidate(base, patch, candidate_id=uuid4())
    assert result.payload_fingerprint == retry.payload_fingerprint
    assert result.candidate.digest != retry.candidate.digest
    assert result.candidate.predecessor_digest == base.digest
    assert (
        base.document()["sections"]["parish"][0]["values"]["name"] == "Example Parish"
    )
    result.patch()[0]["values"]["name"] = "Discarded copy"
    patch[0]["values"]["name"] = "Mutated caller"
    assert result.patch()[0]["values"]["name"] == "New Parish Name"
    assert "New Parish Name" not in repr(result)


def test_integration_add_update_remove_and_order_independence():
    """Disjoint record operations commute; removals do not implicitly recreate IDs."""
    base = configuration_version()
    integration = base.document()["sections"]["integrations"][0]
    addition = {
        "section": "integrations",
        "operation": "add",
        "id": str(uuid4()),
        "values": {
            "kind": "slack",
            "settings": {"channel_id": "example"},
            "credential_fingerprint": None,
        },
    }
    removal = {
        "section": "integrations",
        "operation": "remove",
        "id": integration["id"],
    }
    identifier = uuid4()
    first = build_candidate(base, [addition, removal], candidate_id=identifier)
    second = build_candidate(base, [removal, addition], candidate_id=identifier)
    assert first == second
    assert first.candidate.document()["sections"]["integrations"] == [
        {"id": addition["id"], "values": addition["values"]}
    ]
    update = {
        "section": "integrations",
        "operation": "update",
        "id": integration["id"],
        "values": {"settings": {"organization_id": "54321"}},
    }
    changed = build_candidate(base, [update], candidate_id=uuid4())
    assert (
        changed.candidate.document()["sections"]["integrations"][0]["values"][
            "credential_fingerprint"
        ]
        == "a" * 64
    )


@pytest.mark.parametrize("replacement", [None, {}, [], [None], ["private"], [1] * 101])
def test_patch_container_rejection(replacement):
    """Malformed or excessive intent containers never reach persistence."""
    with pytest.raises(ConfigError):
        build_candidate(configuration_version(), replacement, candidate_id=uuid4())


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation", None),
        ("operation", []),
        ("operation", "toggle"),
        ("section", []),
        ("section", "login_rules"),
        ("id", None),
        ("id", "private-not-an-id"),
        ("values", {}),
        ("values", "private"),
        ("values", {"api_key": "private-secret"}),
        ("values", {"name": 1.25}),
        ("values", {"name": {"private": "secret"}}),
        ("values", {"website": "https://user:private-secret@example.org"}),
        ("private-unknown-field", "private-secret"),
    ],
)
def test_invalid_patch_fields_are_safe(field, value):
    """Neither arbitrary names nor submitted secret-like values enter diagnostics."""
    base = configuration_version()
    patch = parish_patch(base, name="Valid")
    patch[0][field] = value
    with pytest.raises(ConfigError) as error:
        build_candidate(base, patch, candidate_id=uuid4())
    assert "private" not in str(error.value)


@pytest.mark.parametrize("action", ["add", "remove"])
def test_parish_cannot_be_added_or_removed(action):
    """The one parish's stable identity is not replaced through intake."""
    base = configuration_version()
    patch = parish_patch(base, name="Valid")
    patch[0]["operation"] = action
    if action == "remove":
        del patch[0]["values"]
    with pytest.raises(ConfigError):
        build_candidate(base, patch, candidate_id=uuid4())


def test_duplicate_record_and_missing_update_are_rejected():
    """Repeated operations are ambiguous, and deleted targets are never recreated."""
    base = configuration_version()
    patch = parish_patch(base, name="Valid")
    with pytest.raises(ConfigError):
        build_candidate(base, patch * 2, candidate_id=uuid4())
    patch[0]["id"] = str(uuid4())
    with pytest.raises(ConfigError):
        build_candidate(base, patch, candidate_id=uuid4())


def test_add_existing_integration_and_remove_with_values_are_rejected():
    """Add is never upsert; remove cannot smuggle an unvalidated values payload."""
    base = configuration_version()
    integration = base.document()["sections"]["integrations"][0]
    patch = [{"section": "integrations", "operation": "add", **integration}]
    with pytest.raises(ConfigError):
        build_candidate(base, patch, candidate_id=uuid4())
    patch[0]["operation"] = "remove"
    with pytest.raises(ConfigError):
        build_candidate(base, patch, candidate_id=uuid4())


def test_invalid_explicit_types_and_forged_metadata():
    """A caller-constructed ConfigurationVersion cannot bypass canonical parsing."""
    from dataclasses import replace

    base = configuration_version()
    patch = parish_patch(base, name="Valid")
    for candidate_base, candidate_id in ((None, uuid4()), (base, "not-uuid")):
        with pytest.raises(TypeError):
            build_candidate(candidate_base, patch, candidate_id=candidate_id)
    with pytest.raises(ConfigError):
        build_candidate(replace(base, version_id=uuid4()), patch, candidate_id=uuid4())
    with pytest.raises(ConfigError):
        build_candidate(base, patch, candidate_id=base.version_id)


def test_unknown_request_schema_is_not_silently_upgraded():
    """Stored schema discriminators select a frozen builder, never a best guess."""
    base = configuration_version()
    with pytest.raises(ConfigError, match="Unsupported"):
        build_candidate(
            base,
            parish_patch(base, name="Valid"),
            candidate_id=uuid4(),
            request_schema="private-unknown-schema",
        )


@pytest.mark.parametrize(
    "values",
    [
        {
            "kind": "email",
            "settings": {"sender": "x@example.org", "reply_to": "x@example.org"},
        },
        {"kind": "parishsoft"},
        {"credential_fingerprint": "b" * 64},
        {"credential_fingerprint": "a" * 64},
        {"credential_fingerprint": None},
    ],
)
def test_integration_identity_and_secret_evidence_are_not_patchable(values):
    """Ordinary updates cannot rebind a target or manufacture credential evidence."""
    base = configuration_version()
    identifier = base.document()["sections"]["integrations"][0]["id"]
    with pytest.raises(ConfigError):
        build_candidate(
            base,
            [
                {
                    "section": "integrations",
                    "id": identifier,
                    "operation": "update",
                    "values": values,
                }
            ],
            candidate_id=uuid4(),
        )


def test_new_integration_cannot_claim_an_installed_credential():
    """Only the target credential workflow may later establish its fingerprint."""
    with pytest.raises(ConfigError):
        build_candidate(
            configuration_version(),
            [
                {
                    "section": "integrations",
                    "id": str(uuid4()),
                    "operation": "add",
                    "values": {
                        "kind": "slack",
                        "settings": {"channel_id": "example"},
                        "credential_fingerprint": "a" * 64,
                    },
                }
            ],
            candidate_id=uuid4(),
        )
