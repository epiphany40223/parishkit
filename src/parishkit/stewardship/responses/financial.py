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

# A plain dollar amount (digits, commas, up to two decimals); pledge_error uses
# it to tell an amount over the $999,999,999.99 limit from malformed input.
LARGE_PLEDGE = re.compile(r"[1-9][0-9,]*(?:\.[0-9]{1,2})?")


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


def pledge_error(value):
    """The Family form's wording for a pledge that isn't a usable amount.

    A blank pledge is asked for plainly, a well-formed amount of a billion
    dollars or more gets the limit, and anything else is shown the format.
    """
    text = value.strip() if isinstance(value, str) else None
    if text == "":
        return "Enter an annual pledge."
    if (
        text
        and LARGE_PLEDGE.fullmatch(text)
        and int(text.replace(",", "").partition(".")[0]) >= 1_000_000_000
    ):
        return "Enter an annual pledge under $1,000,000,000."
    return "Enter a dollar amount, like 1200 or 1200.50."


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

    A positive pledge requires a frequency and, when any share methods are
    offered, at least one of them. Zero permits an omitted frequency and no
    sharing choice, but does not relax the validity of any supplied frequency,
    option identity or required text. The owner rejects the entire financial
    section when its module is disabled.

    ``cannot_give`` records "Because of financial limitations, I/we cannot
    contribute financially at this time." The form hides the pledge fields
    then, so the answer is a zero pledge with no frequency or share methods.
    """
    if type(options) is not tuple or any(
        not isinstance(option, ShareOption) for option in options
    ):
        raise TypeError("Versioned financial share options are required.")
    allowed = {option.id: option for option in options}
    if len(allowed) != len(options):
        raise TypeError("Financial share option identities must be unique.")
    if type(payload) is dict:
        # Omitted means the box was not checked (older tabs, simple callers).
        payload = {"cannot_give": False} | payload
    if (
        type(payload) is not dict
        or set(payload) != {"annual_pledge", "frequency", "shares", "cannot_give"}
        or type(payload["cannot_give"]) is not bool
    ):
        raise InvalidFinancialAnswers({"financial": "Review the financial fields."})
    if payload["cannot_give"]:
        # The hidden pledge may be blank or zero; nothing else is meaningful.
        # Check types first: an unhashable forged value must be a 422, not
        # a TypeError from the set membership test.
        if (
            type(payload["annual_pledge"]) is not str
            or payload["annual_pledge"] not in {"", "0", "0.00"}
            or payload["frequency"] != ""
            or payload["shares"] != {}
        ):
            raise InvalidFinancialAnswers(
                {"financial": "A Family that cannot contribute enters no pledge."}
            )
        return {
            "annual_pledge": "0.00",
            "frequency": "",
            "shares": {},
            "cannot_give": True,
        }
    errors, amount = {}, None
    try:
        amount = pledge_amount(payload["annual_pledge"])
    except ValueError:
        errors["financial.annual_pledge"] = pledge_error(payload["annual_pledge"])
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
        if (
            amount is not None
            and amount.cents > 0
            and allowed
            and not shares
            and "financial.shares" not in errors
        ):
            errors["financial.shares"] = (
                "Choose at least one way you would like to share your pledge."
            )
    if errors:
        raise InvalidFinancialAnswers(errors)
    return {
        "annual_pledge": amount.canonical,
        "frequency": frequency,
        "shares": normalized,
        "cannot_give": False,
    }
