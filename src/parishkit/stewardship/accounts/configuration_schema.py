"""Strict non-secret schema for the first configuration preparation increment.

This is intentionally not the complete product schema: nonempty sections owned
by later packages fail closed. Extend their schemas before preparing documents
that contain them. Neither arbitrary JSON nor caller-supplied validators may
bypass this boundary when persisting canonical documents.
"""

import hashlib
import re
from functools import cache
from importlib.resources import files
from types import MappingProxyType
from urllib.parse import urlsplit
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, URLValidator

from parishkit.config import ConfigError

VALIDATION_SCHEMA = "parish-integrations-v1"
# Frozen names copied from tzdata 2026.3. This is schema data, not timezone
# transition rules; future dependency updates must not change historical input.
_TIMEZONE_NAMES_SHA256 = (
    "5027e610a10d1983d286e21fa1fb718f0d34704446cb37f707e81707bb3c1244"
)
FINGERPRINT_PATTERN = r"[0-9a-f]{64}"
INTEGRATION_FIELDS = {
    "parishsoft": {"organization_id": "text"},
    "google_oauth": {"client_id": "text"},
    "google_workspace": {"delegated_email": "email"},
    "email": {"sender": "email", "reply_to": "email"},
    "slack": {"channel_id": "text"},
    "backup": {"target": "url"},
}


class SchemaEnvironmentError(RuntimeError):
    """The installation cannot evaluate a schema; this is not invalid user data."""


@cache
def _timezone_names():
    """Load integrity-checked frozen schema data, independent of installed tzdata."""
    try:
        payload = files(__package__).joinpath("timezone_names_v1.txt").read_bytes()
        if hashlib.sha256(payload).hexdigest() != _TIMEZONE_NAMES_SHA256:
            raise SchemaEnvironmentError(
                "The schema timezone catalog is unavailable or invalid."
            )
        return frozenset(payload.decode("utf-8").splitlines())
    except (OSError, UnicodeError, ModuleNotFoundError):
        raise SchemaEnvironmentError(
            "The schema timezone catalog is unavailable or invalid."
        ) from None


def _invalid():
    """Reject without echoing private submitted values or arbitrary field names."""
    raise ConfigError("Invalid or unsupported non-secret configuration fields.")


def _text(value, maximum=254):
    """Bound strings and reject invisible controls and ambiguous whitespace."""
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value)
    ):
        _invalid()


def _typed(value, kind):
    """Check canonical public metadata, including credential-free HTTP(S) URLs."""
    _text(value, 2048 if kind == "url" else 254)
    try:
        if kind == "email":
            EmailValidator()(value)
        elif kind == "url":
            URLValidator(schemes=["https", "http"])(value)
            parsed = urlsplit(value)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                _invalid()
        elif kind == "uuid" and str(UUID(value)) != value:
            _invalid()
    except (ValidationError, ValueError):
        _invalid()


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
        _invalid()
    parishes = sections.get("parish", [])
    if len(parishes) != 1:
        _invalid()
    parish = parishes[0]["values"]
    if set(parish) != {"name", "website", "timezone", "phone", "branding"}:
        _invalid()
    _text(parish["name"])
    _typed(parish["website"], "url")
    _text(parish["timezone"])
    if parish["timezone"] not in _timezone_names():
        _invalid()
    if (
        type(parish["phone"]) is not str
        or re.fullmatch(r"\+1[2-9][0-9]{2}[2-9][0-9]{6}", parish["phone"]) is None
    ):
        _invalid()
    branding = parish["branding"]
    if not isinstance(branding, dict) or set(branding) != {
        "large",
        "menu",
        "icon",
        "favicon",
    }:
        _invalid()
    for reference in branding.values():
        _typed(reference, "uuid")
    seen = set()
    for record in sections.get("integrations", []):
        values = record["values"]
        if set(values) != {"kind", "settings", "credential_fingerprint"}:
            _invalid()
        kind = values["kind"]
        if type(kind) is not str or kind not in INTEGRATION_FIELDS or kind in seen:
            _invalid()
        seen.add(kind)
        settings = values["settings"]
        fields = INTEGRATION_FIELDS[kind]
        if not isinstance(settings, dict) or set(settings) != set(fields):
            _invalid()
        for name, value_type in fields.items():
            _typed(settings[name], value_type)
        fingerprint = values["credential_fingerprint"]
        if fingerprint is not None and (
            type(fingerprint) is not str
            or re.fullmatch(FINGERPRINT_PATTERN, fingerprint) is None
        ):
            _invalid()


# A historical row chooses its validator, not the currently emitted schema.
# Add future validators and a migration admitting their names; retain old
# functions and their schema data unchanged while historical versions exist.
VALIDATORS = MappingProxyType({"parish-integrations-v1": _validate_v1_sections})


def validator_for(schema):
    """Resolve an explicit supported historical discriminator without guessing."""
    try:
        return VALIDATORS[schema]
    except KeyError:
        raise ConfigError("Unsupported configuration validation schema.") from None


def validate_sections(document):
    """Validate a newly prepared document using the current emitted schema."""
    validator_for(VALIDATION_SCHEMA)(document)
