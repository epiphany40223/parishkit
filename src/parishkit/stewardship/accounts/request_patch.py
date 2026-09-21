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

from . import source_cadence_schema as cadence
from .authority import ConfigurationVersion, parse_version
from .chair_confirmation import REQUEST_SCHEMA as CHAIR_REQUEST_SCHEMA
from .configuration_schema import validator_for
from .content_schema import RECOVERY_SCHEMA as CONTENT_RECOVERY_SCHEMA
from .content_schema import REQUEST_SCHEMA as CONTENT_REQUEST_SCHEMA
from .content_schema import SCHEMA as CONTENT_SCHEMA
from .content_schema import validate_content_change
from .ministry_activity import RECOVERY_SCHEMA as MINISTRY_RECOVERY_SCHEMA
from .ministry_activity import REQUEST_SCHEMA as MINISTRY_REQUEST_SCHEMA
from .ministry_activity import SCHEMA as MINISTRY_SCHEMA
from .ministry_activity import remember_records
from .setup_configuration import REQUEST_SCHEMA as SETUP_REQUEST_SCHEMA
from .setup_configuration import build_setup_candidate

REQUEST_SCHEMA = "parish-integrations-patch-v1"
POLICY_REQUEST_SCHEMA = "foundation-policy-patch-v2"
CAMPAIGN_REQUEST_SCHEMA = "campaign-foundation-patch-v3"
CREDENTIAL_REQUEST_SCHEMA = "integration-credential-patch-v6"
MANUAL_POLICY_REQUEST_SCHEMAS = frozenset(
    {
        POLICY_REQUEST_SCHEMA,
        CAMPAIGN_REQUEST_SCHEMA,
        MINISTRY_REQUEST_SCHEMA,
        CONTENT_REQUEST_SCHEMA,
        cadence.REQUEST_SCHEMA,
    }
)


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


def _build_v3_candidate(base, patch, *, candidate_id):
    """Admit campaign drafts and schedules with unchanged manual-policy provenance."""
    from parishkit.stewardship.campaigns.configuration import validate_campaign_change

    from .policy_schema import validate_policy_change

    result = _build_records(
        base,
        patch,
        candidate_id=candidate_id,
        schema="campaign-foundation-v3",
        sections={"parish", "integrations", "login_rules", "campaigns", "schedules"},
    )
    validate_policy_change(
        base.document()["sections"].get("login_rules", []),
        result.candidate.document()["sections"].get("login_rules", []),
    )
    validate_campaign_change(base.document(), result.candidate.document())
    return result


def _build_v4_candidate(base, patch, *, candidate_id):
    """Add local activity without weakening campaign or policy change validation."""
    from parishkit.stewardship.campaigns.configuration import validate_campaign_change

    from .policy_schema import validate_policy_change

    result = _build_records(
        base,
        patch,
        candidate_id=candidate_id,
        schema=MINISTRY_SCHEMA,
        sections={
            "parish",
            "integrations",
            "login_rules",
            "campaigns",
            "schedules",
            "ministries",
        },
    )
    old, new = base.document(), result.candidate.document()
    validate_policy_change(
        old["sections"].get("login_rules", []),
        new["sections"].get("login_rules", []),
    )
    validate_campaign_change(old, new)
    by_id, by_identity = {}, {}
    remember_records(old, by_id, by_identity)
    remember_records(new, by_id, by_identity)
    return result


def _build_v5_candidate(base, patch, *, candidate_id, schema=CONTENT_SCHEMA):
    """Add immutable content while retaining policy, campaign and Ministry fences."""
    from parishkit.stewardship.campaigns.configuration import validate_campaign_change

    from .policy_schema import validate_policy_change

    result = _build_records(
        base,
        patch,
        candidate_id=candidate_id,
        schema=schema,
        sections={
            "parish",
            "integrations",
            "login_rules",
            "campaigns",
            "schedules",
            "ministries",
            "content",
        },
    )
    old, new = base.document(), result.candidate.document()
    validate_policy_change(
        old["sections"].get("login_rules", []),
        new["sections"].get("login_rules", []),
    )
    validate_campaign_change(old, new)
    by_id, by_identity = {}, {}
    remember_records(old, by_id, by_identity)
    remember_records(new, by_id, by_identity)
    validate_content_change(old, new)
    return result


def _credential_schema_v6(document):
    """Freeze v6 receipt replay against its original five supported public schemas."""
    sections = document["sections"]
    if "content" in sections:
        return "campaign-content-v5"
    if sections.get("ministries"):
        return "ministry-activity-v4"
    if any(sections.get(name) for name in ("campaigns", "schedules")):
        return "campaign-foundation-v3"
    if sections.get("login_rules"):
        return "foundation-policy-v2"
    return "parish-integrations-v1"


def _build_credential_candidate(base, patch, *, candidate_id, schema=None):
    """A separate explicit format changes one fingerprint, never public settings.

    Parsing binds immutable intent, not installed-credential authority. The
    selection owner and installer verify target receipts/ACKs independently.
    Default and retained v1-v5 parsers continue to reject fingerprint edits.
    """
    if not isinstance(base, ConfigurationVersion):
        raise TypeError("An explicit configuration version is required.")
    if (
        type(patch) is not list
        or len(patch) != 1
        or type(patch[0]) is not dict
        or patch[0].get("operation") != "update"
        or patch[0].get("section") != "integrations"
        or type(patch[0].get("values")) is not dict
        or set(patch[0]["values"]) != {"credential_fingerprint"}
        or patch[0]["values"]["credential_fingerprint"] is None
    ):
        _invalid()
    result = _build_records(
        base,
        patch,
        candidate_id=candidate_id,
        schema=schema or _credential_schema_v6(base.document()),
        sections={"integrations"},
        credential_reference=True,
    )
    record = next(
        row
        for row in result.candidate.document()["sections"]["integrations"]
        if row["id"] == patch[0]["id"]
    )
    if record["values"]["kind"] not in {"parishsoft", "google_workspace", "slack"}:
        _invalid()
    return result


def _build_records(
    base, patch, *, candidate_id, schema, sections, credential_reference=False
):
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
                elif "kind" in values or (
                    "credential_fingerprint" in values and not credential_reference
                ):
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
def _build_recovery_candidate(
    base, patch, *, candidate_id, schema="foundation-policy-v2"
):
    """The dedicated recovery format admits only an additive exact-address Admin."""
    result = _build_records(
        base,
        patch,
        candidate_id=candidate_id,
        schema=schema,
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
            or new["creation_operation"] != new["grants"]["administrator"].get("manual")
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


def _build_recovery_v2_candidate(base, patch, *, candidate_id):
    """Preserve additive-only operator recovery after campaign schema adoption."""
    return _build_recovery_candidate(
        base, patch, candidate_id=candidate_id, schema="campaign-foundation-v3"
    )


def _build_recovery_bootstrap_candidate(base, patch, *, candidate_id):
    """Allow additive offline Admin recovery before the setup wizard completes."""
    return _build_recovery_candidate(
        base, patch, candidate_id=candidate_id, schema="bootstrap-policy-v1"
    )


def _build_recovery_ministry_candidate(base, patch, *, candidate_id):
    """Retain local activity during additive-only offline Admin recovery."""
    return _build_recovery_candidate(
        base, patch, candidate_id=candidate_id, schema=MINISTRY_SCHEMA
    )


def _build_recovery_content_candidate(base, patch, *, candidate_id):
    """Preserve selected content during additive-only offline Admin recovery."""
    return _build_recovery_candidate(
        base, patch, candidate_id=candidate_id, schema=CONTENT_SCHEMA
    )


def _build_cadence_candidate(base, patch, *, candidate_id):
    """Preserve every content, campaign and policy edit rule with nightly settings."""
    return _build_v5_candidate(
        base, patch, candidate_id=candidate_id, schema=cadence.SCHEMA
    )


def _build_recovery_cadence_candidate(base, patch, *, candidate_id):
    """Offline recovery remains additive-only without dropping selected cadence."""
    return _build_recovery_candidate(
        base, patch, candidate_id=candidate_id, schema=cadence.SCHEMA
    )


def _build_credential_cadence_candidate(base, patch, *, candidate_id):
    """Fingerprint-only selection retains public cadence and frozen v6 replay."""
    return _build_credential_candidate(
        base, patch, candidate_id=candidate_id, schema=cadence.SCHEMA
    )


def credential_request_schema(document):
    """Select the new receipt parser only for configuration containing cadence."""
    return (
        cadence.CREDENTIAL_SCHEMA
        if cadence.uses_cadence(document)
        else CREDENTIAL_REQUEST_SCHEMA
    )


def _build_chair_seed_candidate(base, patch, *, candidate_id):
    """Admit exactly a Chairperson confirmation's effects over the newest schema.

    The confirmation is the sole creator of Chairperson-seeded authority, so
    its validator replaces the ordinary policy-change rule; every other fence
    of the newest format holds, and the cadence setting stays optional.
    """
    from parishkit.stewardship.campaigns.configuration import validate_campaign_change

    from .chair_confirmation import validate_seed_change

    result = _build_records(
        base,
        patch,
        candidate_id=candidate_id,
        schema=cadence.SCHEMA,
        sections={
            "parish",
            "integrations",
            "login_rules",
            "campaigns",
            "schedules",
            "ministries",
            "content",
        },
    )
    old, new = base.document(), result.candidate.document()
    validate_seed_change(
        old["sections"].get("login_rules", []),
        new["sections"].get("login_rules", []),
        str(candidate_operation(patch)),
    )
    validate_campaign_change(old, new)
    by_id, by_identity = {}, {}
    remember_records(old, by_id, by_identity)
    remember_records(new, by_id, by_identity)
    validate_content_change(old, new)
    return result


def candidate_operation(patch):
    """The one operation identity a confirmation patch names, or refuse.

    A confirmation binds every seeded origin and assignment to the request
    that carries it; the request identity is not known to the builder, so the
    patch must name exactly one, which intake and the installer then hold to
    the request's own identity.
    """
    operations = {
        item["values"].get("operation_id")
        for item in patch
        if type(item) is dict
        and type(item.get("values")) is dict
        and item["values"].get("kind") == "assignment"
    }
    if len(operations) != 1:
        _invalid()
    return operations.pop()


BUILDERS = MappingProxyType(
    {
        "parish-integrations-patch-v1": _build_v1_candidate,
        POLICY_REQUEST_SCHEMA: _build_v2_candidate,
        CAMPAIGN_REQUEST_SCHEMA: _build_v3_candidate,
        MINISTRY_REQUEST_SCHEMA: _build_v4_candidate,
        MINISTRY_RECOVERY_SCHEMA: _build_recovery_ministry_candidate,
        CONTENT_REQUEST_SCHEMA: _build_v5_candidate,
        CONTENT_RECOVERY_SCHEMA: _build_recovery_content_candidate,
        CREDENTIAL_REQUEST_SCHEMA: _build_credential_candidate,
        SETUP_REQUEST_SCHEMA: build_setup_candidate,
        cadence.REQUEST_SCHEMA: _build_cadence_candidate,
        cadence.RECOVERY_SCHEMA: _build_recovery_cadence_candidate,
        cadence.CREDENTIAL_SCHEMA: _build_credential_cadence_candidate,
        "operator-recovery-patch-v1": _build_recovery_candidate,
        "operator-recovery-patch-v2": _build_recovery_v2_candidate,
        "operator-recovery-bootstrap-v1": _build_recovery_bootstrap_candidate,
        CHAIR_REQUEST_SCHEMA: _build_chair_seed_candidate,
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
    if (
        isinstance(base, ConfigurationVersion) and cadence.uses_cadence(base.document())
    ) or (
        type(patch) is list
        and any(
            type(item) is dict
            and item.get("section") == "integrations"
            and type(item.get("values")) is dict
            and type(item["values"].get("settings")) is dict
            and "nightly_time" in item["values"]["settings"]
            for item in patch
        )
    ):
        return cadence.REQUEST_SCHEMA
    if (
        isinstance(base, ConfigurationVersion)
        and "content" in base.document()["sections"]
    ) or (
        type(patch) is list
        and any(
            type(item) is dict and item.get("section") == "content" for item in patch
        )
    ):
        return CONTENT_REQUEST_SCHEMA
    if (
        isinstance(base, ConfigurationVersion)
        and base.document()["sections"].get("ministries")
    ) or (
        type(patch) is list
        and any(
            type(item) is dict and item.get("section") == "ministries" for item in patch
        )
    ):
        return MINISTRY_REQUEST_SCHEMA
    if (
        isinstance(base, ConfigurationVersion)
        and any(
            base.document()["sections"].get(name) for name in ("campaigns", "schedules")
        )
    ) or (
        type(patch) is list
        and any(
            type(item) is dict and item.get("section") in ("campaigns", "schedules")
            for item in patch
        )
    ):
        return CAMPAIGN_REQUEST_SCHEMA
    policy = isinstance(base, ConfigurationVersion) and base.document()["sections"].get(
        "login_rules"
    )
    touches_policy = type(patch) is list and any(
        type(item) is dict and item.get("section") == "login_rules" for item in patch
    )
    return POLICY_REQUEST_SCHEMA if policy or touches_policy else REQUEST_SCHEMA
