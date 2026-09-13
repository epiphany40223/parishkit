"""Setup intent cannot weaken ordinary edits or replace bootstrap authority."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.bootstrap_schema import bootstrap_version
from parishkit.stewardship.accounts.request_patch import build_candidate
from parishkit.stewardship.accounts.setup_configuration import REQUEST_SCHEMA

from .campaign_factory import campaign
from .configuration_factory import configuration_document, configuration_version
from .policy_factory import address


def setup_patch():
    """Public fake values include fingerprints, never working provider credentials."""
    sections = configuration_document()["sections"]
    sections["campaigns"] = [campaign()]
    for kind, settings in (
        ("google_workspace", {"delegated_email": "mail@example.org"}),
        ("email", {"sender": "mail@example.org", "reply_to": "reply@example.org"}),
    ):
        sections["integrations"].append(
            {
                "id": str(uuid4()),
                "values": {
                    "kind": kind,
                    "settings": settings,
                    "credential_fingerprint": None if kind == "email" else "b" * 64,
                },
            }
        )
    return [
        {"operation": "add", "section": section, **row}
        for section, rows in sections.items()
        for row in rows
    ]


def build(base, patch, candidate_id=None):
    """Exercise explicit dispatch without admitting ordinary request persistence."""
    return build_candidate(
        base,
        patch,
        candidate_id=candidate_id or uuid4(),
        request_schema=REQUEST_SCHEMA,
    )


def test_setup_intent_is_canonical_additive_and_detached():
    """Original access is identical and retries cannot reinterpret the candidate."""
    base = bootstrap_version(uuid4(), "admin@example.org")
    patch = setup_patch()
    original = deepcopy(patch)
    identifier = uuid4()
    result = build(base, patch, identifier)
    assert result == build(base, list(reversed(patch)), identifier)
    assert result.payload_fingerprint == build(base, patch).payload_fingerprint
    assert result.candidate.predecessor_digest == base.digest
    assert (
        result.candidate.document()["sections"]["login_rules"]
        == (base.document()["sections"]["login_rules"])
    )
    assert patch == original
    patch[0]["values"]["name"] = "Discarded caller mutation"
    assert "Discarded" not in str(result.patch())
    assert "Example Parish" not in repr(result)


def test_setup_is_never_selected_implicitly_or_for_a_configured_base():
    """Only the future setup finalizer can explicitly request this transition."""
    base = bootstrap_version(uuid4(), "admin@example.org")
    with pytest.raises(ConfigError):
        build_candidate(base, setup_patch(), candidate_id=uuid4())
    with pytest.raises(ConfigError):
        build(configuration_version(), setup_patch())


@pytest.mark.parametrize("action", ["add", "update", "remove"])
def test_setup_cannot_modify_or_replace_an_existing_admin(action):
    """Even an unchanged resubmission cannot change the original record's identity."""
    base = bootstrap_version(uuid4(), "admin@example.org")
    row = base.document()["sections"]["login_rules"][0]
    operation = {"operation": action, "section": "login_rules", **row}
    if action == "remove":
        del operation["values"]
    with pytest.raises(ConfigError):
        build(base, setup_patch() + [operation])


def test_setup_preserves_recovered_bootstrap_admins_too():
    """A valid bootstrap successor need not be the original singleton root."""
    from parishkit.stewardship.accounts.authority import parse_version
    from parishkit.stewardship.accounts.bootstrap_schema import (
        validate_bootstrap_sections,
    )

    root = bootstrap_version(uuid4(), "admin@example.org")
    document = root.document()
    document["version_id"] = str(uuid4())
    document["predecessor_digest"] = root.digest
    document["sections"]["login_rules"].append(address("recovery@example.org"))
    base = parse_version(document, validate_sections=validate_bootstrap_sections)
    assert (
        build(base, setup_patch()).candidate.document()["sections"]["login_rules"]
        == (base.document()["sections"]["login_rules"])
    )


@pytest.mark.parametrize("section", ["parish", "campaigns", "integrations"])
def test_setup_requires_complete_core_sections(section):
    """A partial public profile is staging, not a finalizable candidate."""
    patch = [row for row in setup_patch() if row["section"] != section]
    with pytest.raises(ConfigError):
        build(bootstrap_version(uuid4(), "admin@example.org"), patch)


@pytest.mark.parametrize("kind", ["parishsoft", "google_workspace", "email"])
def test_setup_requires_each_integration(kind):
    """Email rendering settings and Workspace delivery authority are distinct."""
    patch = [row for row in setup_patch() if row["values"].get("kind") != kind]
    with pytest.raises(ConfigError):
        build(bootstrap_version(uuid4(), "admin@example.org"), patch)


@pytest.mark.parametrize("kind", ["parishsoft", "google_workspace"])
def test_setup_requires_nonsecret_fingerprint_references(kind):
    """The parser does not manufacture readiness for an unbound credential."""
    patch = setup_patch()
    next(row for row in patch if row["values"].get("kind") == kind)["values"][
        "credential_fingerprint"
    ] = None
    with pytest.raises(ConfigError):
        build(bootstrap_version(uuid4(), "admin@example.org"), patch)


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation", "update"),
        ("section", []),
        ("id", "private-not-a-uuid"),
        ("values", {"api_key": "private-secret"}),
        ("private-extra-field", "private-secret"),
    ],
)
def test_setup_refuses_malformed_or_secret_values_without_echo(field, value):
    """Malformed request diagnostics contain no caller-supplied values or keys."""
    patch = setup_patch()
    patch[0][field] = value
    with pytest.raises(ConfigError) as error:
        build(bootstrap_version(uuid4(), "admin@example.org"), patch)
    assert "private" not in str(error.value)


def test_setup_cannot_add_two_campaigns_or_mismatched_timezone():
    """Only one initial campaign shares the parish's configured civil-day zone."""
    base = bootstrap_version(uuid4(), "admin@example.org")
    patch = setup_patch()
    with pytest.raises(ConfigError):
        build(
            base, patch + [{"operation": "add", "section": "campaigns", **campaign()}]
        )
    next(row for row in patch if row["section"] == "campaigns")["values"][
        "timezone"
    ] = "America/Chicago"
    with pytest.raises(ConfigError):
        build(base, patch)


def test_setup_rejects_forged_identity_and_duplicate_global_ids():
    """Canonical base metadata and global record uniqueness remain mandatory."""
    base = bootstrap_version(uuid4(), "admin@example.org")
    patch = setup_patch()
    with pytest.raises(ConfigError):
        build(replace(base, version_id=uuid4()), patch)
    with pytest.raises(ConfigError):
        build(base, patch, base.version_id)
    patch[1]["id"] = patch[0]["id"]
    with pytest.raises(ConfigError):
        build(base, patch)


@pytest.mark.parametrize("patch", [None, {}, [], [None], [1] * 301])
def test_setup_request_is_bounded(patch):
    """Malformed containers and excess operations are refused before persistence."""
    with pytest.raises(ConfigError):
        build(bootstrap_version(uuid4(), "admin@example.org"), patch)
