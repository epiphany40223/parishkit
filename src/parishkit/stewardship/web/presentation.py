"""Shared exact US display rules; dates and instants deliberately stay distinct."""

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext

from django.utils.translation import gettext as _

from parishkit.stewardship.campaigns.domain import Money, Percentage


def number(value):
    """Group integers/finite decimal values without introducing binary floats."""
    if type(value) is not int and not isinstance(value, Decimal):
        raise ValueError("Display numbers must be exact integers or decimals.")
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError("Display numbers must be finite.")
    if abs(value) >= 10**30 or (
        isinstance(value, Decimal) and value.as_tuple().exponent < -12
    ):
        raise ValueError("Display number exceeds the supported bound.")
    return format(value, ",f") if isinstance(value, Decimal) else format(value, ",d")


def usd(value):
    """Use the canonical cents value, including source adjustments below zero."""
    if not isinstance(value, Money):
        raise TypeError("Currency formatting requires Money.")
    dollars, cents = divmod(abs(value.cents), 100)
    return f"{'-' if value.cents < 0 else ''}${dollars:,}.{cents:02d}"


def percentage(value):
    """Round once, half up, to one fractional digit; omit an exact trailing zero."""
    if not isinstance(value, Percentage):
        raise TypeError("Percentage formatting requires exact count inputs.")
    amount = value.value
    if amount is None:
        return "—"
    with localcontext(prec=40, rounding=ROUND_HALF_UP):
        rounded = amount.quantize(Decimal("0.1"))
    return number(int(rounded) if rounded == int(rounded) else rounded) + "%"


def out_of(value):
    """Keep the count/percentage sentence together for future localization."""
    if not isinstance(value, Percentage):
        raise TypeError("Participation formatting requires Percentage.")
    return _("%(count)s out of %(total)s (%(percentage)s)") % {
        "count": number(value.numerator),
        "total": number(value.denominator),
        "percentage": percentage(value),
    }


def instant(value):
    """Emit a UTC ISO instant for client localization, never guess a naive zone."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timestamp formatting requires a timezone-aware instant.")
    return value.astimezone(UTC).isoformat()


def parish_date(value):
    """A campaign-local calendar date is never shifted into the browser timezone."""
    if type(value) is not date:
        raise ValueError("Campaign date formatting requires a calendar date.")
    return value.strftime("%B") + f" {value.day}, {value.year}"
