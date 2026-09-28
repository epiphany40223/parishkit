"""Shared exact US display rules; dates and instants deliberately stay distinct."""

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext

import phonenumbers
from django.utils.translation import gettext as _

from parishkit.stewardship.campaigns.domain import Money, Percentage

from . import dates


def campaign_year(values):
    """One campaign-year meaning for Admin previews, page blocks and share labels.

    An explicit label always wins. Otherwise a renewal campaign usually runs in
    the autumn before the stewardship year it asks about, so the upcoming
    financial period's start year is the year Families expect to read. Only a
    campaign without a financial period falls back to its own start year.
    """
    if values.get("year_label"):
        return values["year_label"]
    financial = values.get("financial")
    if financial and financial.get("start"):
        return financial["start"][:4]
    return values["start_date"][:4]


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


def duid(value):
    """Show a ParishSoft identifier exactly as ParishSoft does, never grouped.

    DUIDs (Family, Member, Ministry, Fund, ...) are identifiers, not counts,
    so thousands separators would only make them harder to match and search.
    ParishSoft Family IDs may be negative, so a leading minus is kept.
    """
    if type(value) is int:
        return str(value)
    if (
        type(value) is str
        and value.removeprefix("-").isascii()
        and value.removeprefix("-").isdecimal()
    ):
        return value
    raise ValueError("A DUID must be an exact integer.")


def _split_extension(value):
    """Separate a ";ext=" suffix, the stored E.164 form's extension marker."""
    number, marker, extension = value.partition(";ext=")
    return number, extension if marker else ""


def phone(value):
    """Show one telephone number as "+1 (502) 555-1234", however it was stored.

    Storage stays canonical E.164 (optionally with ";ext="); source data may
    hold national text such as "502-555-1234". North American numbers use the
    parish's familiar grouping, other countries phonenumbers' international
    form. Text that is not a plausible number is shown unchanged rather than
    guessed at, so malformed source values stay recognizable.
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    number, extension = _split_extension(text)
    try:
        parsed = phonenumbers.parse(number, "US")
    except phonenumbers.NumberParseException:
        return text
    extension = extension or parsed.extension or ""
    national = str(parsed.national_number)
    if parsed.country_code == 1:
        # Only a complete number (ten digits, or eleven with the leading 1)
        # is regrouped. A seven-digit local number, or a foreign national
        # number such as "020 7183 8750", stays as written rather than
        # being turned into an invented +1 number.
        digits = "".join(char for char in number if char.isdigit())
        if parsed.extension and digits.endswith(parsed.extension):
            digits = digits[: -len(parsed.extension)]
        if len(national) != 10 or digits not in (national, "1" + national):
            return text
        shown = f"+1 ({national[:3]}) {national[3:6]}-{national[6:]}"
    elif number.startswith("+") and phonenumbers.is_valid_number(parsed):
        # Other countries only when written with their + country code.
        parsed.extension = None
        shown = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
        )
    else:
        return text
    return f"{shown} ext. {extension}" if extension else shown


def parse_us_phone(value):
    """Accept any common way of writing a US number; return canonical E.164.

    "(502) 555-1234", "502-555-1234", "502.555.1234" and "+1 502 555 1234"
    all become "+15025551234". Raises ValueError for anything that is not a
    valid ten-digit North American number, the only kind the parish profile
    stores.
    """
    text = str(value or "").strip()
    try:
        parsed = phonenumbers.parse(text, "US")
    except phonenumbers.NumberParseException:
        raise ValueError("Not a telephone number.") from None
    if parsed.country_code != 1 or parsed.extension:
        raise ValueError("Only a North American number without extension.")
    canonical = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    national = canonical[2:]
    # Mirror the stored constraint: area code and exchange cannot start 0 or 1.
    if len(national) != 10 or national[0] in "01" or national[3] in "01":
        raise ValueError("Not a valid North American number.")
    return canonical


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


def parish_date(value, style=None):
    """A campaign-local calendar date is never shifted into the browser timezone.

    ``style`` is the parish ``date_format`` code; request rendering leaves it
    unset and uses the request's parish setting (see :mod:`.dates`).
    """
    if type(value) is not date:
        raise ValueError("Campaign date formatting requires a calendar date.")
    return dates.format_date(value, style)


def parish_instant(value, timezone, style=None):
    """Show an instant in the campaign's time zone, e.g. "September 28, 2026 at
    7:15 AM EDT", so every Family and Admin sees the same wall-clock time."""
    return dates.format_instant(value, timezone, style)
