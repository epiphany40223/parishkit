"""Frozen shared configuration primitives; semantics are retained across schemas."""

import hashlib
from functools import cache
from importlib.resources import files
from urllib.parse import urlsplit
from uuid import UUID

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, URLValidator

from parishkit.config import ConfigError

# Keep the historical schema asset and its checksum unchanged.
SCHEMA_ASSET_PACKAGE = "parishkit.stewardship.accounts"
_TIMEZONE_NAMES_SHA256 = (
    "5027e610a10d1983d286e21fa1fb718f0d34704446cb37f707e81707bb3c1244"
)


class SchemaEnvironmentError(RuntimeError):
    """The installation cannot evaluate a schema; this is not invalid user data."""


@cache
def timezone_names():
    """Load integrity-checked frozen schema data, independent of installed tzdata."""
    try:
        payload = (
            files(SCHEMA_ASSET_PACKAGE).joinpath("timezone_names_v1.txt").read_bytes()
        )
        if hashlib.sha256(payload).hexdigest() != _TIMEZONE_NAMES_SHA256:
            raise SchemaEnvironmentError(
                "The schema timezone catalog is unavailable or invalid."
            )
        return frozenset(payload.decode("utf-8").splitlines())
    except (OSError, UnicodeError, ModuleNotFoundError):
        raise SchemaEnvironmentError(
            "The schema timezone catalog is unavailable or invalid."
        ) from None


def invalid():
    """Reject without echoing private submitted values or arbitrary field names."""
    raise ConfigError("Invalid or unsupported non-secret configuration fields.")


def text(value, maximum=254):
    """Bound strings and reject invisible controls and ambiguous whitespace."""
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value)
    ):
        invalid()


def typed(value, kind):
    """Check canonical public metadata, including credential-free HTTP(S) URLs."""
    text(value, 2048 if kind == "url" else 254)
    try:
        if kind == "email":
            EmailValidator()(value)
        elif kind == "url":
            URLValidator(schemes=["https", "http"])(value)
            parsed = urlsplit(value)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                invalid()
        elif kind == "uuid" and str(UUID(value)) != value:
            invalid()
    except (ValidationError, ValueError):
        invalid()
