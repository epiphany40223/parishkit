"""Deterministic, bounded source payloads with no lossy numeric conversions.

Adapters normalize their field semantics first. Dates are ISO strings and money
is an exact decimal string, never a binary float. Mapping order is irrelevant;
array order is meaningful and must be normalized by the collection's adapter.
No input value is included in a validation error.
"""

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal


class InvalidSourcePayload(ValueError):
    """A source payload cannot participate in a validated coherent snapshot."""


def _normalize(value, depth=0):
    """Accept only bounded JSON data and explicit lossless domain scalar types."""
    if depth > 16:
        raise InvalidSourcePayload("Source payload nesting exceeds its bound.")
    if value is None or type(value) in (bool, int):
        if type(value) is int and not -(2**63) <= value < 2**63:
            raise InvalidSourcePayload("Source integer exceeds its supported range.")
        return value
    if type(value) is str:
        if "\0" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise InvalidSourcePayload("Source text contains unsupported characters.")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidSourcePayload(
                "Source timestamps require an explicit timezone."
            )
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if (
            not value.is_finite()
            or abs(value) >= Decimal("1e20")
            or abs(value.as_tuple().exponent) > 30
        ):
            raise InvalidSourcePayload(
                "Source amount is not finite or is out of range."
            )
        text = format(value, "f")
        return (text.rstrip("0").rstrip(".") if "." in text else text) if value else "0"
    if type(value) is dict:
        if len(value) > 10000 or any(type(key) is not str for key in value):
            raise InvalidSourcePayload("Source mappings require bounded string keys.")
        return {
            _normalize(key, depth + 1): _normalize(item, depth + 1)
            for key, item in value.items()
        }
    if type(value) is list:
        if len(value) > 10000:
            raise InvalidSourcePayload("Source array exceeds its bound.")
        return [_normalize(item, depth + 1) for item in value]
    raise InvalidSourcePayload("Source payload contains an unsupported value type.")


def canonical_payload(payload):
    """Return canonical UTF-8 JSON text and its mandatory SHA-256 content digest."""
    if type(payload) is not dict:
        raise InvalidSourcePayload("Source entity payload must be a mapping.")
    text = json.dumps(
        _normalize(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    encoded = text.encode("utf-8")
    if len(encoded) > 1024 * 1024:
        raise InvalidSourcePayload("Source entity payload exceeds its byte limit.")
    return text, hashlib.sha256(encoded).hexdigest()
