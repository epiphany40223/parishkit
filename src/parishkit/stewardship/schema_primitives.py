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


@cache
def timezone_links():
    """Map each catalog alias to the catalog zone it links to (#1286).

    The catalog admits every IANA "backward" link name (US/Eastern,
    Canada/Pacific, Japan, ...). The mapping is read from the installed
    tzdata package's own link lines (``L target alias`` in ``tzdata.zi``),
    not kept by hand, and only links whose two names are both in the frozen
    catalog count. The database's ``stewardship_timezone_name_v1`` holds the
    same table; a PostgreSQL test keeps the two equal.
    """
    try:
        payload = files("tzdata").joinpath("zoneinfo", "tzdata.zi").read_text("utf-8")
    except (OSError, UnicodeError, ModuleNotFoundError):
        raise SchemaEnvironmentError(
            "The installed time zone data is unavailable or invalid."
        ) from None
    names = timezone_names()
    links = {}
    for line in payload.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[0] == "L" and {*fields[1:]} <= names:
            links[fields[2]] = fields[1]
    return links


def canonical_timezone(zone):
    """The zone a catalog alias links to (US/Eastern -> America/New_York).

    Any other name, canonical or not in the catalog, is returned unchanged,
    so callers still validate it against ``timezone_names``.
    """
    return timezone_links().get(zone, zone)


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
    text(value, 2048 if kind in {"url", "link"} else 254)
    try:
        if kind == "email":
            EmailValidator()(value)
        elif kind in {"url", "link"}:
            URLValidator(schemes=["https", "http"])(value)
            parsed = urlsplit(value)
            if parsed.username or parsed.password:
                invalid()
            # "url" (e.g. the parish website) also refuses a query or fragment
            # so tokens cannot hide in stored identity URLs. A "link" is a
            # destination Families are sent to (e.g. online giving), and real
            # giving providers need query parameters such as "?tab=home".
            if kind == "url" and (parsed.query or parsed.fragment):
                invalid()
        elif kind == "uuid" and str(UUID(value)) != value:
            invalid()
    except (ValidationError, ValueError):
        invalid()
