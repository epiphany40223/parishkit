"""Typed comparison values shared by form baselines and proposal reconciliation.

This is comparison, not answer validation or display formatting. Callers retain
the original display value and separately validate the owning field's required,
range and availability rules. In particular, unavailable upstream data must not
be passed as a fabricated blank. Only an explicitly available value is compared.
The version is part of the trusted form dependency projection.
"""

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

COMPARISON_VERSION = "family-comparison-v1"
ADDRESS_COMPONENTS = ("line1", "line2", "city", "region", "postal_code", "country")


class ValueKind(StrEnum):
    """Closed canonical types; field registries choose these, never browser input."""

    TEXT = "text"
    EMAIL = "email"
    PHONE = "phone"
    DATE = "date"
    BOOLEAN = "boolean"
    IDENTIFIER = "identifier"
    MONEY = "money"
    ENUM = "enum"
    ADDRESS = "address"


class ComparisonValueError(ValueError):
    """A malformed typed input cannot be compared; its private value is omitted."""


def _invalid():
    """Never interpolate private input, even when the field type is unexpected."""
    raise ComparisonValueError(
        "The field has no valid canonical comparison value."
    ) from None


def _text(value):
    """Normalize Unicode and outer whitespace, preserving meaningful internal text."""
    if (
        type(value) is not str
        or len(value) > 8192
        or "\0" in value
        or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        _invalid()
    return unicodedata.normalize("NFC", value).strip()


def _money(value):
    """Use exact integer cents, never float coercion, rounding or huge exponents."""
    if type(value) not in (str, int, Decimal):
        _invalid()
    if type(value) is int and abs(value) >= 10**18:
        _invalid()
    if isinstance(value, str) and len(value) > 40:
        _invalid()
    try:
        amount = Decimal(value)
        if not amount.is_finite():
            _invalid()
        # The schema owns nonnegative/maximum pledge validation. Comparison also
        # serves upstream amounts; do not round a third decimal into equivalence.
        sign, digits, exponent = amount.as_tuple()
        if not -18 <= exponent <= 18 or len(digits) > 40:
            _invalid()
        coefficient = int("".join(map(str, digits)))
        if exponent >= -2:
            cents, remainder = coefficient * 10 ** (exponent + 2), 0
        else:
            cents, remainder = divmod(coefficient, 10 ** (-exponent - 2))
        if remainder or cents >= 10**20:
            _invalid()
        return -cents if sign else cents
    except (InvalidOperation, ValueError, OverflowError):
        _invalid()


def _identifier(value):
    """Unify positive provider IDs and UUID renderings without treating bool as int."""
    if isinstance(value, UUID):
        return ("uuid", str(value))
    if type(value) is int:
        if not 0 < value < 2**63:
            _invalid()
        return ("integer", value)
    if type(value) is str and len(value) <= 36:
        if re.fullmatch(r"[1-9][0-9]{0,18}", value):
            return _identifier(int(value))
        try:
            return ("uuid", str(UUID(value)))
        except ValueError:
            pass
    _invalid()


def _phone(value, *, dialing_region):
    """Compare dialable components without guessing an unknown country context.

    Formatting punctuation is ignored only for a recognized numeric telephone
    shape. A US context explicitly maps ten-digit national numbers (or eleven
    with leading 1) to +1. Other national numbers remain distinct from explicit
    international numbers. Extensions remain part of the identity. Invalid
    source text stays opaque rather than collapsing different errors to empty.
    """
    value = _text(value)
    if len(value) > 256 or dialing_region not in (None, "US"):
        _invalid()
    match = re.fullmatch(
        r"(?P<number>\+?[0-9][0-9 ()\.\-]*|\([0-9][0-9 ()\.\-]*)"
        r"(?:\s*(?:ext\.?|x|#|;ext=)\s*(?P<extension>[0-9]{1,12}))?",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return ("opaque", value)
    number = match["number"]
    digits = re.sub(r"[^0-9]", "", number)
    if not 1 <= len(digits) <= 15:
        return ("opaque", value)
    international = number.startswith("+")
    if not international and dialing_region == "US":
        if len(digits) == 10:
            digits, international = "1" + digits, True
        elif len(digits) == 11 and digits.startswith("1"):
            international = True
    return (
        "international" if international else "national",
        digits,
        match["extension"] or "",
    )


def canonical_value(kind, value, *, dialing_region=None):
    """Return an immutable typed key while leaving the original display untouched.

    None represents an explicit known null, never an unavailable source field.
    Email comparison is case-insensitive but preserves plus tags and dots.
    Enum inputs have already been mapped to the owning schema's canonical codes.
    Addresses require the complete normalized component set, so omitted fields
    cannot silently become empty values in a form concurrency fingerprint.
    """
    if not isinstance(kind, ValueKind):
        _invalid()
    if value is None:
        return None
    if kind in {ValueKind.TEXT, ValueKind.ENUM}:
        return _text(value)
    if kind is ValueKind.EMAIL:
        return _text(value).casefold()
    if kind is ValueKind.PHONE:
        return _phone(value, dialing_region=dialing_region)
    if kind is ValueKind.BOOLEAN:
        if type(value) is not bool:
            _invalid()
        return value
    if kind is ValueKind.DATE:
        if type(value) is date:
            return value.isoformat()
        if type(value) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            try:
                return date.fromisoformat(value).isoformat()
            except ValueError:
                pass
        _invalid()
    if kind is ValueKind.IDENTIFIER:
        return _identifier(value)
    if kind is ValueKind.MONEY:
        return _money(value)
    if kind is ValueKind.ADDRESS:
        if type(value) is not dict or set(value) != set(ADDRESS_COMPONENTS):
            _invalid()
        return tuple(
            (component, _text(value[component]).casefold())
            for component in ADDRESS_COMPONENTS
        )
    _invalid()
