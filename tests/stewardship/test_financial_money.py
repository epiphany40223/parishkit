"""Shared money arithmetic never guesses zero, loses cents or leaks raw errors."""

from decimal import Decimal, localcontext

import pytest

from parishkit.stewardship.reports.money import (
    MoneyAmount,
    source_cents,
    source_total,
)


@pytest.mark.parametrize(
    "cents,canonical,display",
    [
        (None, None, "Unavailable"),
        (0, "0.00", "$0.00"),
        (1, "0.01", "$0.01"),
        (-1, "-0.01", "-$0.01"),
        (123456789, "1234567.89", "$1,234,567.89"),
        (-123456789, "-1234567.89", "-$1,234,567.89"),
    ],
)
def test_money_availability_and_lossless_us_display(cents, canonical, display):
    """Zero, missing and signed adjustments stay distinct through serialization."""
    amount = MoneyAmount(cents)
    assert amount.available is (cents is not None)
    assert amount.canonical == canonical and amount.display == display
    assert amount.decimal == (Decimal(canonical) if canonical is not None else None)
    assert amount.document() == {"available": cents is not None, "amount": canonical}


@pytest.mark.parametrize("value", [True, False, 1.0, Decimal("1.00"), "1", []])
def test_money_rejects_lossy_or_ambiguous_internal_types(value):
    """Internal aggregate constructors do not silently reinterpret input units."""
    with pytest.raises(TypeError, match="integer cents"):
        MoneyAmount(value)


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        1,
        Decimal("1.00"),
        "",
        "1",
        "1.0",
        "1.001",
        "01.00",
        "-0.00",
        "+1.00",
        "1,000.00",
        "1e2",
        "NaN",
        "Infinity",
        "100000000000000000000.00",
        "١.00",
        "1.00\n",
        "private-value",
    ],
)
def test_source_amounts_require_canonical_exact_cents(value):
    """Reject malformed source data rather than turning it into an aggregate."""
    with pytest.raises(ValueError) as failure:
        source_cents(value)
    assert str(failure.value) == "Source money must contain canonical exact USD cents."


def test_large_signed_totals_are_independent_of_decimal_context():
    """Provider amounts exceed pledge limits; even a tiny Decimal context is safe."""
    maximum = "99999999999999999999.99"
    with localcontext() as context:
        context.prec = 3
        amount = source_total([maximum, maximum, "-0.01"], available=True)
        assert amount.canonical == "199999999999999999999.97"
        assert amount.decimal == Decimal("199999999999999999999.97")
        assert amount.display == "$199,999,999,999,999,999,999.97"
    assert source_cents("-12.34") == -1234


def test_complete_empty_and_incomplete_observations_are_not_equivalent():
    """Incomplete data is not inspected or totaled into a misleading zero."""
    assert source_total([], available=True).cents == 0
    assert source_total(["private-invalid-value"], available=False).cents is None
    with pytest.raises(TypeError, match="availability"):
        source_total([], available=1)
