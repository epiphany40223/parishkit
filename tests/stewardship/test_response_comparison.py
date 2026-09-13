"""Canonical equality preserves types, availability and Family display provenance."""

from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from uuid import UUID

import pytest

from parishkit.stewardship.responses.comparison import (
    ADDRESS_COMPONENTS,
    ComparisonValueError,
    ValueKind,
    canonical_value,
)


@pytest.mark.parametrize(
    ("kind", "left", "right"),
    [
        (ValueKind.TEXT, "  Jose\u0301  ", "José"),
        (ValueKind.EMAIL, " Family+Tag@Example.org ", "family+tag@example.org"),
        (ValueKind.DATE, date(2024, 2, 29), "2024-02-29"),
        (ValueKind.BOOLEAN, False, False),
        (ValueKind.MONEY, "1.00", Decimal("1")),
        (ValueKind.MONEY, 0, "0.00"),
        (ValueKind.MONEY, "1E2", "100.00"),
        (ValueKind.IDENTIFIER, 123, "123"),
        (
            ValueKind.IDENTIFIER,
            UUID("12345678-1234-1234-1234-123456789abc"),
            "12345678-1234-1234-1234-123456789ABC",
        ),
        (ValueKind.ENUM, " married ", "married"),
    ],
)
def test_typed_equivalent_values(kind, left, right):
    """Representation changes alone do not create proposals or stale forms."""
    assert canonical_value(kind, left) == canonical_value(kind, right)


@pytest.mark.parametrize(
    ("kind", "left", "right"),
    [
        (ValueKind.TEXT, "John", "JOHN"),
        (ValueKind.TEXT, "Mary Ann", "Mary  Ann"),
        (ValueKind.EMAIL, "a.b@example.org", "ab@example.org"),
        (ValueKind.EMAIL, "a+tag@example.org", "a@example.org"),
        (ValueKind.TEXT, None, ""),
        (ValueKind.MONEY, None, "0.00"),
        (ValueKind.ENUM, "married", "single"),
        (ValueKind.IDENTIFIER, 1, UUID(int=1)),
    ],
)
def test_meaningful_differences_remain_distinct(kind, left, right):
    """Do not collapse meaningful text, provider aliases, unknowns or ID domains."""
    assert canonical_value(kind, left) != canonical_value(kind, right)


@pytest.mark.parametrize("kind", list(ValueKind))
def test_explicit_null_is_not_a_fabricated_value(kind):
    """Availability is a separate caller-owned bit, never inferred from null."""
    assert canonical_value(kind, None) is None


@pytest.mark.parametrize(
    "value",
    ["(202) 555-0123", "202.555.0123", "1 (202) 555-0123", "+1 202 555 0123"],
)
def test_phone_equivalence_requires_explicit_country_context(value):
    """US national formatting equals an international form only with known context."""
    assert canonical_value(ValueKind.PHONE, value, dialing_region="US") == (
        "international",
        "12025550123",
        "",
    )


@pytest.mark.parametrize("suffix", ["ext 42", "EXT.42", "x42", "#42", ";ext=42"])
def test_phone_extensions_are_preserved(suffix):
    """Extensions are dialable identity, not decoration to discard."""
    assert canonical_value(ValueKind.PHONE, "+44 20 1234 5678 " + suffix) == (
        "international",
        "442012345678",
        "42",
    )


def test_unknown_phone_country_and_invalid_source_text_are_not_guessed():
    """No heuristic creates equality between an ambiguous number and a known one."""
    assert canonical_value(ValueKind.PHONE, "2025550123") != canonical_value(
        ValueKind.PHONE, "+12025550123"
    )
    assert canonical_value(ValueKind.PHONE, "call the office") != canonical_value(
        ValueKind.PHONE, "call the house"
    )
    assert canonical_value(ValueKind.PHONE, "") == ("opaque", "")


def test_address_normalization_preserves_display_and_component_identity():
    """Component ordering and case do not matter; meaningful content still does."""
    original = dict.fromkeys(ADDRESS_COMPONENTS, "") | {
        "line1": " 1 Example St ",
        "city": "Montréal",
        "country": "CA",
    }
    equivalent = dict(reversed(list(original.items()))) | {
        "line1": "1 example st",
        "city": "Montre\u0301al",
        "country": "ca",
    }
    before = original.copy()
    assert canonical_value(ValueKind.ADDRESS, original) == canonical_value(
        ValueKind.ADDRESS, equivalent
    )
    assert original == before
    assert canonical_value(ValueKind.ADDRESS, original) != canonical_value(
        ValueKind.ADDRESS, original | {"line2": "Unit 2"}
    )


def test_money_comparison_does_not_depend_on_process_decimal_context():
    """A caller's low precision cannot round two different pledges into equality."""
    with localcontext() as context:
        context.prec = 2
        assert canonical_value(ValueKind.MONEY, "1234.56") == 123456
        assert canonical_value(ValueKind.MONEY, "1234.57") == 123457


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("untrusted-kind", None),
        (ValueKind.TEXT, 42),
        (ValueKind.TEXT, "x" * 8193),
        (ValueKind.TEXT, "private\0text"),
        (ValueKind.TEXT, "private\ud800text"),
        (ValueKind.BOOLEAN, 1),
        (ValueKind.BOOLEAN, "false"),
        (ValueKind.DATE, "2025-02-29"),
        (ValueKind.DATE, "20240101"),
        (ValueKind.DATE, datetime(2024, 1, 1, tzinfo=UTC)),
        (ValueKind.MONEY, 1.01),
        (ValueKind.MONEY, True),
        (ValueKind.MONEY, "1.001"),
        (ValueKind.MONEY, "NaN"),
        (ValueKind.MONEY, "Infinity"),
        (ValueKind.MONEY, "1e1000000000"),
        (ValueKind.MONEY, "0e-1000000000"),
        (ValueKind.MONEY, "private-invalid-value"),
        (ValueKind.IDENTIFIER, True),
        (ValueKind.IDENTIFIER, "01"),
        (ValueKind.IDENTIFIER, 0),
        (ValueKind.IDENTIFIER, 2**63),
        (ValueKind.ADDRESS, {}),
        (ValueKind.ADDRESS, dict.fromkeys(ADDRESS_COMPONENTS, None)),
        (ValueKind.ADDRESS, dict.fromkeys(ADDRESS_COMPONENTS, "") | {"extra": ""}),
    ],
)
def test_invalid_comparison_types_do_not_echo_private_values(kind, value):
    """Comparison fails closed without interpolating arbitrary input into errors."""
    with pytest.raises(ComparisonValueError) as caught:
        canonical_value(kind, value)
    assert str(caught.value) == "The field has no valid canonical comparison value."
