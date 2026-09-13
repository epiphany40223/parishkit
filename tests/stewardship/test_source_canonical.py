"""Stable source digests and lossless, bounded normalization without providers."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from parishkit.stewardship.source.canonical import (
    InvalidSourcePayload,
    canonical_payload,
)


def test_mapping_order_is_irrelevant_but_array_order_is_meaningful():
    """Repeated provider key order changes do not create new payload versions."""
    assert canonical_payload(
        {"b": [1, 2], "a": {"y": False, "x": None}}
    ) == canonical_payload({"a": {"x": None, "y": False}, "b": [1, 2]})
    assert canonical_payload({"a": [1, 2]}) != canonical_payload({"a": [2, 1]})


def test_exact_money_unicode_dates_and_utc_instants():
    """Do not round money, escape away Unicode, or retain local timezone offsets."""
    canonical, digest = canonical_payload(
        {
            "name": "José",
            "amount": Decimal("12.3400"),
            "date": date(2026, 9, 11),
            "instant": datetime(2026, 9, 11, 8, tzinfo=timezone(timedelta(hours=-4))),
        }
    )
    assert canonical == (
        '{"amount":"12.34","date":"2026-09-11",'
        '"instant":"2026-09-11T12:00:00+00:00","name":"José"}'
    )
    assert len(digest) == 64
    assert canonical_payload({"amount": Decimal("-0.00")}) == canonical_payload(
        {"amount": "0"}
    )
    assert canonical_payload(
        {"instant": datetime(2026, 9, 11, 12, tzinfo=UTC)}
    ) == canonical_payload({"instant": "2026-09-11T12:00:00+00:00"})


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        1.2,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1e21"),
        Decimal("1e-100000"),
        datetime(2026, 9, 11),
        2**63,
        -(2**63) - 1,
        "secret\0value",
        "secret\ud800value",
        {1: "secret"},
        set(),
        (1, 2),
    ],
)
def test_unsupported_scalars_fail_without_echoing_private_values(value):
    """Unsupported upstream data aborts staging instead of a lossy conversion."""
    with pytest.raises(InvalidSourcePayload) as error:
        canonical_payload({"secret-key": value})
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("value", [[None] * 10001, "a" * (1024 * 1024)])
def test_payload_size_bounds(value):
    """Do not admit unbounded arrays or entity payloads into durable storage."""
    with pytest.raises(InvalidSourcePayload):
        canonical_payload({"field": value})


def test_depth_bound_and_mapping_root():
    """Reject deeply nested cycles and non-entity roots with safe diagnostics."""
    value = {}
    value["self"] = value
    with pytest.raises(InvalidSourcePayload, match="nesting"):
        canonical_payload(value)
    with pytest.raises(InvalidSourcePayload, match="mapping"):
        canonical_payload([])


def test_decimal_precision_is_not_limited_by_context_rounding():
    """Canonicalization preserves every supported source decimal digit."""
    value = Decimal("123456789.123456789123456789123456789")
    assert canonical_payload({"value": value}) == canonical_payload(
        {"value": str(value)}
    )
