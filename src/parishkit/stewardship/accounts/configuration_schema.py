"""Strict non-secret schema for the first configuration preparation increment.

This is intentionally not the complete product schema: nonempty sections owned
by later packages fail closed. Extend their schemas before preparing documents
that contain them. Neither arbitrary JSON nor caller-supplied validators may
bypass this boundary when persisting canonical documents.
"""

import re
from types import MappingProxyType

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import validate_campaign_sections
from parishkit.stewardship.schema_primitives import (
    SchemaEnvironmentError as SchemaEnvironmentError,
)
from parishkit.stewardship.schema_primitives import (
    invalid,
    text,
    timezone_names,
    typed,
)

from .bootstrap_schema import BOOTSTRAP_SCHEMA, validate_bootstrap_sections

VALIDATION_SCHEMA = "parish-integrations-v1"
FINGERPRINT_PATTERN = r"[0-9a-f]{64}"
INTEGRATION_FIELDS = {
    "parishsoft": {"organization_id": "text"},
    "google_oauth": {"client_id": "text"},
    "google_workspace": {"delegated_email": "email"},
    "email": {"sender": "email", "reply_to": "email"},
    "slack": {"channel_id": "text"},
    "backup": {"target": "url"},
}


def _validate_v1_sections(document):
    """Validate an envelope-normalized document before any database writes.

    Only the configured parish profile and non-secret integration metadata are
    admitted here. Branding references identify already normalized media objects;
    upload decoding and existence checks remain ARC-03/ADM-03 responsibilities.
    """
    sections = document["sections"]
    if any(
        records
        for name, records in sections.items()
        if name not in {"parish", "integrations"}
    ):
        invalid()
    parishes = sections.get("parish", [])
    if len(parishes) != 1:
        invalid()
    parish = parishes[0]["values"]
    if set(parish) != {"name", "website", "timezone", "phone", "branding"}:
        invalid()
    text(parish["name"])
    typed(parish["website"], "url")
    text(parish["timezone"])
    if parish["timezone"] not in timezone_names():
        invalid()
    if (
        type(parish["phone"]) is not str
        or re.fullmatch(r"\+1[2-9][0-9]{2}[2-9][0-9]{6}", parish["phone"]) is None
    ):
        invalid()
    branding = parish["branding"]
    if not isinstance(branding, dict) or set(branding) != {
        "large",
        "menu",
        "icon",
        "favicon",
    }:
        invalid()
    for reference in branding.values():
        typed(reference, "uuid")
    seen = set()
    for record in sections.get("integrations", []):
        values = record["values"]
        if set(values) != {"kind", "settings", "credential_fingerprint"}:
            invalid()
        kind = values["kind"]
        if type(kind) is not str or kind not in INTEGRATION_FIELDS or kind in seen:
            invalid()
        seen.add(kind)
        settings = values["settings"]
        fields = INTEGRATION_FIELDS[kind]
        if not isinstance(settings, dict) or set(settings) != set(fields):
            invalid()
        for name, value_type in fields.items():
            typed(settings[name], value_type)
        fingerprint = values["credential_fingerprint"]
        if fingerprint is not None and (
            type(fingerprint) is not str
            or re.fullmatch(FINGERPRINT_PATTERN, fingerprint) is None
        ):
            invalid()


# A historical row chooses its validator, not the currently emitted schema.
# Add future validators and a migration admitting their names; retain old
# functions and their schema data unchanged while historical versions exist.
def _validate_v2_sections(document):
    """Extend the frozen parish schema with explicitly versioned login policy."""
    from .policy_schema import validate_policy_records

    policy = document["sections"].get("login_rules", [])
    base = document | {
        "sections": {
            key: value
            for key, value in document["sections"].items()
            if key != "login_rules"
        }
    }
    _validate_v1_sections(base)
    validate_policy_records(policy)


def _validate_v3_sections(document):
    """Add campaign configuration without reinterpreting historical policy schemas."""
    if not document["sections"].get("login_rules"):
        invalid()
    base = document | {
        "sections": {
            name: records
            for name, records in document["sections"].items()
            if name not in {"campaigns", "schedules"}
        }
    }
    _validate_v2_sections(base)
    validate_campaign_sections(document)


VALIDATORS = MappingProxyType(
    {
        "parish-integrations-v1": _validate_v1_sections,
        "foundation-policy-v2": _validate_v2_sections,
        "campaign-foundation-v3": _validate_v3_sections,
        BOOTSTRAP_SCHEMA: validate_bootstrap_sections,
    }
)


def validator_for(schema):
    """Resolve an explicit supported historical discriminator without guessing."""
    try:
        return VALIDATORS[schema]
    except KeyError:
        raise ConfigError("Unsupported configuration validation schema.") from None


def validate_sections(document):
    """Validate a newly prepared document using the current emitted schema."""
    validator_for(schema_for(document))(document)


def schema_for(document):
    """Keep legacy documents on their retained schema until new policy is present."""
    if set(document["sections"]) == {"login_rules"}:
        return BOOTSTRAP_SCHEMA
    if any(document["sections"].get(name) for name in ("campaigns", "schedules")):
        return "campaign-foundation-v3"
    return (
        "foundation-policy-v2"
        if document["sections"].get("login_rules")
        else VALIDATION_SCHEMA
    )
