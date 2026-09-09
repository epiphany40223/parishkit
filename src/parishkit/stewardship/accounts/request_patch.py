"""Bounded stable-record patches for the configuration preparation subset.

This is an internal intent format, not a public JSON Patch API. Operations on
distinct records commute and canonicalize by section/ID. An update replaces
named top-level values (including a complete nested settings/branding value),
never recursively guesses how to merge an object or recreates a missing record.
"""

import hashlib
import json
from dataclasses import dataclass, field
from types import MappingProxyType
from uuid import UUID

from parishkit.config import ConfigError

from .authority import ConfigurationVersion, parse_version
from .configuration_schema import validator_for

REQUEST_SCHEMA = "parish-integrations-patch-v1"
POLICY_REQUEST_SCHEMA = "foundation-policy-patch-v2"


def _invalid():
    """Do not echo submitted values, keys, IDs, or possibly private target names."""
    raise ConfigError("Invalid or unsupported configuration patch.")


@dataclass(frozen=True)
class PatchedConfiguration:
    """Immutable validated candidate and canonical bytes of its minimal intent."""

    candidate: ConfigurationVersion
    canonical_patch: bytes = field(repr=False)
    payload_fingerprint: str

    def patch(self):
        """Return a fresh JSON value, not a mutable reference into the intent."""
        return json.loads(self.canonical_patch)


def _build_v1_candidate(base, patch, *, candidate_id):
    """Validate up to 100 disjoint operations and the complete resulting schema.

    Only parish updates and integration additions/updates/removals are implemented.
    Login-rule, recovery, campaign, content, and schedule edits remain disabled until
    their concrete schemas and admission policies land. No raw secret is admitted.
    """
    return _build_records(
        base,
        patch,
        candidate_id=candidate_id,
        schema="parish-integrations-v1",
        sections={"parish", "integrations"},
    )


def _build_v2_candidate(base, patch, *, candidate_id):
    """Admit normal manual policy edits without changing retained v1 retry behavior."""
    from .policy_schema import validate_policy_change

    result = _build_records(
        base,
        patch,
        candidate_id=candidate_id,
        schema="foundation-policy-v2",
        sections={"parish", "integrations", "login_rules"},
    )
    validate_policy_change(
        base.document()["sections"].get("login_rules", []),
        result.candidate.document()["sections"].get("login_rules", []),
    )
    return result


def _build_records(base, patch, *, candidate_id, schema, sections):
    """Shared mechanical patch application; each stored parser chooses its schema."""
    if not isinstance(base, ConfigurationVersion) or not isinstance(candidate_id, UUID):
        raise TypeError("Explicit configuration and candidate identities are required.")
    if candidate_id == base.version_id:
        _invalid()
    if type(patch) is not list or not 1 <= len(patch) <= 100:
        _invalid()
    document = base.document()
    validate_sections = validator_for(schema)
    # Revalidate the base too: the caller cannot manufacture an invalid envelope.
    if parse_version(document, validate_sections=validate_sections) != base:
        _invalid()
    document["version_id"] = str(candidate_id)
    document["predecessor_digest"] = base.digest
    normalized = []
    seen = set()
    for operation in patch:
        if type(operation) is not dict:
            _invalid()
        action = operation.get("operation")
        if type(action) is not str or action not in {"add", "update", "remove"}:
            _invalid()
        fields = {"operation", "section", "id"}
        if action != "remove":
            fields.add("values")
        if set(operation) != fields:
            _invalid()
        section, identifier = operation["section"], operation["id"]
        if type(section) is not str or section not in sections:
            _invalid()
        if section == "parish" and action != "update":
            _invalid()
        try:
            if type(identifier) is not str or str(UUID(identifier)) != identifier:
                _invalid()
        except ValueError:
            _invalid()
        if (section, identifier) in seen:
            _invalid()
        seen.add((section, identifier))
        records = document["sections"].setdefault(section, [])
        existing = next((item for item in records if item["id"] == identifier), None)
        if (action == "add") != (existing is None):
            _invalid()
        item = {"operation": action, "section": section, "id": identifier}
        if action == "remove":
            records.remove(existing)
        else:
            values = operation["values"]
            if type(values) is not dict or not values:
                _invalid()
            if section == "integrations":
                if action == "add":
                    if values.get("credential_fingerprint") is not None:
                        _invalid()
                elif "kind" in values or "credential_fingerprint" in values:
                    # These are identity/installer evidence, not Admin-editable
                    # settings. Even resubmitting an unchanged value is refused.
                    _invalid()
            if action == "add":
                records.append({"id": identifier, "values": values})
            else:
                existing["values"].update(values)
            item["values"] = values
        normalized.append(item)
    candidate = parse_version(document, validate_sections=validate_sections)
    # The complete schema/envelope validation above rejects non-JSON values,
    # excessive nesting/size, unknown fields, and credential-bearing syntax.
    canonical_patch = json.dumps(
        sorted(normalized, key=lambda item: (item["section"], item["id"])),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = hashlib.sha256(
        base.digest.encode("ascii") + canonical_patch
    ).hexdigest()
    return PatchedConfiguration(candidate, canonical_patch, fingerprint)


# Freeze each accepted intent's parser and candidate schema. Future schemas add
# another builder and database-admitted name; retries dispatch the stored name.
def _build_recovery_candidate(base, patch, *, candidate_id):
    """The dedicated recovery format admits only an additive exact-address Admin."""
    result = _build_records(
        base,
        patch,
        candidate_id=candidate_id,
        schema="foundation-policy-v2",
        sections={"login_rules"},
    )
    if len(patch) != 1 or patch[0]["operation"] not in {"add", "update"}:
        _invalid()
    operation = patch[0]
    identifier = operation["id"]
    old = next(
        (
            item["values"]
            for item in base.document()["sections"].get("login_rules", [])
            if item["id"] == identifier
        ),
        None,
    )
    new = next(
        item["values"]
        for item in result.candidate.document()["sections"]["login_rules"]
        if item["id"] == identifier
    )
    if new["kind"] != "address" or (old is not None and old["kind"] != "address"):
        _invalid()
    if old is None:
        if (
            new["roles"] != ["administrator"]
            or new["creation_origin"] != "manual"
            or set(new["grants"]["administrator"]) != {"manual"}
        ):
            _invalid()
    else:
        if "administrator" in old["roles"] or set(operation["values"]) != {
            "roles",
            "grants",
        }:
            _invalid()
        expected = old | {
            "roles": sorted([*old["roles"], "administrator"]),
            "grants": old["grants"]
            | {"administrator": new["grants"].get("administrator")},
        }
        if new != expected or set(new["grants"]["administrator"]) != {"manual"}:
            _invalid()
    return result


BUILDERS = MappingProxyType(
    {
        "parish-integrations-patch-v1": _build_v1_candidate,
        POLICY_REQUEST_SCHEMA: _build_v2_candidate,
        "operator-recovery-patch-v1": _build_recovery_candidate,
    }
)


def build_candidate(base, patch, *, candidate_id, request_schema=None):
    """Dispatch a stored request format, or the current format for new intent."""
    try:
        builder = BUILDERS[
            default_schema(base, patch) if request_schema is None else request_schema
        ]
    except (KeyError, TypeError):
        raise ConfigError("Unsupported configuration request schema.") from None
    return builder(base, patch, candidate_id=candidate_id)


def default_schema(base, patch):
    """Choose a new intent schema while keeping every stored retry discriminator."""
    policy = isinstance(base, ConfigurationVersion) and base.document()["sections"].get(
        "login_rules"
    )
    touches_policy = type(patch) is list and any(
        type(item) is dict and item.get("section") == "login_rules" for item in patch
    )
    return POLICY_REQUEST_SCHEMA if policy or touches_policy else REQUEST_SCHEMA
