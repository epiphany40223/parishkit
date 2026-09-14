"""Pure final financial answers and exact installment calculations.

These values express a Family's intent, not payment instructions. The final
submission owner supplies the campaign's versioned options and persists only
after all enabled sections and concurrency checks pass.
"""

import re
from dataclasses import dataclass

from parishkit.stewardship.reports.money import MoneyAmount

from .census import clean_text

MAX_PLEDGE_CENTS = 99_999_999_999
SHARE_TEXT_LIMIT = 2000
FREQUENCIES = {"weekly": 52, "monthly": 12, "quarterly": 4, "annual": 1}
PLEDGE_INPUT = re.compile(
    r"(?:[0-9]{1,9}|[0-9]{1,3}(?:,[0-9]{3}){1,2})(?:\.[0-9]{1,2})?"
)


class InvalidFinancialAnswers(ValueError):
    """Static field messages only; never interpolate a pledge or free-text answer."""

    def __init__(self, fields):
        """Keep answer values out of exceptions and diagnostics."""
        self.fields = dict(fields)
        super().__init__("Please review the financial stewardship fields.")


@dataclass(frozen=True)
class ShareOption:
    """One trusted campaign-versioned option, identified independently of its label."""

    id: str
    label: str
    free_text: bool


def pledge_amount(value):
    """Normalize bounded ordinary US decimal input, without signs or exponents."""
    value = clean_text(value, 24)
    if value is None or PLEDGE_INPUT.fullmatch(value) is None:
        raise ValueError(
            "Enter a nonnegative annual USD pledge with up to two decimals."
        )
    whole, _, fraction = value.replace(",", "").partition(".")
    cents = int(whole) * 100 + int(fraction.ljust(2, "0"))
    if cents > MAX_PLEDGE_CENTS:
        raise ValueError("The annual pledge exceeds the supported maximum.")
    return MoneyAmount(cents)


def installment(annual, frequency):
    """Round a nonnegative installment half up; the annual total is authoritative."""
    if (
        not isinstance(annual, MoneyAmount)
        or annual.cents is None
        or not 0 <= annual.cents <= MAX_PLEDGE_CENTS
        or type(frequency) is not str
        or frequency not in FREQUENCIES
    ):
        raise ValueError("A valid annual pledge and frequency are required.")
    periods = FREQUENCIES[frequency]
    return MoneyAmount((annual.cents + periods // 2) // periods)


def household_pronoun(count):
    """Count effective active Members, including proposed and excluding terminal."""
    if type(count) is not int or count < 0:
        raise ValueError("An effective household count is required.")
    return "This household" if count == 0 else "I" if count == 1 else "We"


def validate_financial_answers(payload, options):
    """Validate a complete enabled section against server-owned stable share IDs.

    Zero permits an omitted frequency and no sharing choice, but does not relax
    the validity of any supplied frequency, option identity or required text.
    The owner rejects the entire financial section when its module is disabled.
    """
    if type(options) is not tuple or any(
        not isinstance(option, ShareOption) for option in options
    ):
        raise TypeError("Versioned financial share options are required.")
    allowed = {option.id: option for option in options}
    if len(allowed) != len(options):
        raise TypeError("Financial share option identities must be unique.")
    if type(payload) is not dict or set(payload) != {
        "annual_pledge",
        "frequency",
        "shares",
    }:
        raise InvalidFinancialAnswers({"financial": "Review the financial fields."})
    errors, amount = {}, None
    try:
        amount = pledge_amount(payload["annual_pledge"])
    except ValueError:
        errors["financial.annual_pledge"] = (
            "Enter an annual pledge from $0.00 to $999,999,999.99, "
            "with up to two decimals."
        )
    frequency = payload["frequency"]
    if (
        type(frequency) is not str
        or frequency not in {"", *FREQUENCIES}
        or (amount is not None and amount.cents > 0 and frequency == "")
    ):
        errors["financial.frequency"] = (
            "Select weekly, monthly, quarterly or annual for a positive pledge."
        )
    shares = payload["shares"]
    normalized = {}
    if type(shares) is not dict or not set(shares) <= allowed.keys():
        errors["financial.shares"] = "Select from the currently offered share methods."
    else:
        for key in sorted(shares):
            text = clean_text(shares[key], SHARE_TEXT_LIMIT)
            option = allowed[key]
            if (
                text is None
                or (option.free_text and not text)
                or (not option.free_text and text)
            ):
                errors[f"financial.shares.{key}"] = (
                    "Provide the requested text within the displayed length limit."
                    if option.free_text
                    else "This share method does not accept additional text."
                )
            else:
                normalized[key] = text
    if errors:
        raise InvalidFinancialAnswers(errors)
    return {
        "annual_pledge": amount.canonical,
        "frequency": frequency,
        "shares": normalized,
    }
