"""Exact USD aggregates with explicit unavailable values, independent of storage.

Source adjustments may be negative and larger than a Family's allowed new
pledge. Integer cents keep aggregation independent of Decimal context precision
and avoid binary floating point. Only canonical two-decimal strings cross the
JSON boundary; a missing observation is never an observed zero.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

SOURCE_AMOUNT = re.compile(r"-?(?:0|[1-9][0-9]{0,19})\.[0-9]{2}")


@dataclass(frozen=True)
class MoneyAmount:
    """A known signed number of cents, or an explicitly unavailable observation."""

    cents: int | None

    def __post_init__(self):
        """Boolean/float coercion must not manufacture an exact financial amount."""
        if self.cents is not None and type(self.cents) is not int:
            raise TypeError("Money requires integer cents or unavailable.")

    @property
    def available(self):
        """Observed zero is available; missing data is not."""
        return self.cents is not None

    @property
    def canonical(self):
        """Return a lossless JSON-safe amount without using context-sensitive math."""
        if self.cents is None:
            return None
        whole, fraction = divmod(abs(self.cents), 100)
        return f"{'-' if self.cents < 0 else ''}{whole}.{fraction:02d}"

    @property
    def decimal(self):
        """Materialize exact Decimal cents for existing durable numeric columns."""
        return Decimal(self.canonical) if self.available else None

    @property
    def display(self):
        """Use US grouping and two decimals, including signed source adjustments."""
        if self.cents is None:
            return "Unavailable"
        whole, fraction = divmod(abs(self.cents), 100)
        return f"{'-' if self.cents < 0 else ''}${whole:,}.{fraction:02d}"

    def document(self):
        """Expose availability separately from a safe string, never a JSON float."""
        return {"available": self.available, "amount": self.canonical}


def source_cents(value):
    """Validate a normalized provider amount without imposing new-pledge limits."""
    if type(value) is not str or SOURCE_AMOUNT.fullmatch(value) is None:
        raise ValueError("Source money must contain canonical exact USD cents.")
    negative = value.startswith("-")
    whole, fraction = value.removeprefix("-").split(".")
    cents = int(whole) * 100 + int(fraction)
    if negative and cents == 0:
        raise ValueError("Source money must contain canonical exact USD cents.")
    return -cents if negative else cents


def source_total(amounts, *, available):
    """Sum only a proven complete observation; an empty complete set is zero."""
    if type(available) is not bool:
        raise TypeError("Source availability must be explicit.")
    if not available:
        return MoneyAmount(None)
    return MoneyAmount(sum(source_cents(value) for value in amounts))
