"""Closed non-terminal Member fields and side-effect-free census validation.

The browser sends strings, including the explicit ``unknown`` birth-date
choice. Persisted phones retain both a comparison number and the entered
display form. Unknown upstream enum/phone values may be retained unchanged,
but never authorize arbitrary new values. No lookup sends personal data out.
"""

import re
from dataclasses import dataclass
from datetime import date

import phonenumbers

from parishkit.config import ConfigError
from parishkit.parishsoft import split_email_addresses
from parishkit.stewardship.accounts.policy_schema import normalized_email

from .census import clean_text
from .comparison import ComparisonValueError, ValueKind, canonical_value, phone_record
from .merge import KnownValue


@dataclass(frozen=True)
class CensusField:
    """One server-owned editable field and its source/comparison/UI contract."""

    name: str
    source_name: str
    label: str
    kind: ValueKind = ValueKind.TEXT
    required: bool = False
    max_length: int = 100
    choices: tuple[str, ...] = ()


GENDERS = ("Male", "Female", "Unspecified")
MARITAL_STATUSES = (
    "",
    "Annulled",
    "Divorced",
    "Married",
    "Single",
    "Separated",
    "Widowed",
)
UNAVAILABLE = KnownValue(False)
MEMBER_FIELDS = (
    CensusField("prefix", "salutation", "Prefix"),
    CensusField("first_name", "firstName", "First name", required=True),
    CensusField("middle_name", "middleName", "Middle name"),
    CensusField("last_name", "lastName", "Last name", required=True),
    CensusField("suffix", "suffix", "Suffix"),
    CensusField("nickname", "nickName", "Nickname"),
    CensusField("maiden_name", "maidenName", "Maiden name"),
    CensusField("birth_date", "birthdate", "Birth date", ValueKind.DATE, True, 10),
    CensusField("gender", "sex", "Gender", ValueKind.ENUM, True, 100, GENDERS),
    CensusField("email", "email", "Email address", ValueKind.EMAIL, max_length=254),
    CensusField("home_phone", "home", "Home phone", ValueKind.PHONE, max_length=100),
    CensusField(
        "mobile_phone", "mobile", "Mobile phone", ValueKind.PHONE, max_length=100
    ),
    CensusField("work_phone", "work", "Work phone", ValueKind.PHONE, max_length=100),
    CensusField(
        "marital_status",
        "maritalStatus",
        "Marital status",
        ValueKind.ENUM,
        choices=MARITAL_STATUSES,
    ),
    CensusField("language", "language", "Primary spoken language", required=True),
)


class InvalidMemberValue(ValueError):
    """An exception containing only a static, safe field explanation."""


class InvalidMemberSource(ValueError):
    """Retained source cannot safely supply this bounded form's input contract."""


def source_value(field, value):
    """Normalize only documented shapes while retaining unsupported source text.

    Presence remains the caller's responsibility. Blank gender is not a silent
    Unspecified answer, and blank language is not a fabricated English choice.
    """
    if value is None:
        return None
    cleaned = clean_text(value, field.max_length)
    if cleaned is None or any(char in cleaned for char in "\n\r\t\u0085\u2028\u2029"):
        raise InvalidMemberSource("The Member source field is unavailable.")
    try:
        canonical_value(field.kind, value)
    except ComparisonValueError:
        raise InvalidMemberSource("The Member source field is unavailable.") from None
    if field.kind is ValueKind.PHONE:
        return phone_record(value) if value else ""
    if field.choices and type(value) is str:
        cleaned = value.strip()
        for choice in field.choices:
            if cleaned.casefold() == choice.casefold():
                return choice
    return value


def browser_value(field, known, *, previous_unknown=False):
    """Convert only effective values to controls, never expose competing source."""
    value = known.value
    if field.kind is ValueKind.DATE:
        return value or ("unknown" if previous_unknown else "")
    if field.kind is ValueKind.PHONE and isinstance(value, dict):
        return value["display"]
    return value or ""


def _date(value, today):
    """Require a civil date or explicit Unknown, with no host-timezone inference."""
    if value == "unknown":
        return None
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError
        parsed = date.fromisoformat(value)
        if parsed > today:
            raise ValueError
        return parsed.isoformat()
    except ValueError:
        raise InvalidMemberValue(
            "Enter a valid birth date, or choose Unknown."
        ) from None


def _phone(value, source):
    """Validate locally with numbering metadata and explicit US national context.

    International input uses a leading + country code. Extensions are retained.
    Existing malformed source numbers can remain unchanged, not become a new
    fabricated dialable value. This does not verify ownership or deliverability.
    """
    record = phone_record(value)
    if source.available and record == source.value:
        return record
    try:
        parsed = phonenumbers.parse(value, "US")
        if (
            not phonenumbers.is_possible_number(parsed)
            or record["normalized"] is None
            or (parsed.extension and len(parsed.extension) > 12)
        ):
            raise ValueError
        expected = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164
        )
        if parsed.extension:
            expected += ";ext=" + parsed.extension
        if record["normalized"] != expected:
            raise ValueError
    except (ValueError, phonenumbers.NumberParseException):
        raise InvalidMemberValue(
            "Enter a complete phone number; include + and country code outside the US."
        ) from None
    return record


def validate_member_value(field, raw, source=UNAVAILABLE, *, today):
    """Normalize one explicit browser field against trusted current source only."""
    value = clean_text(raw, field.max_length)
    if value is None or any(char in value for char in "\n\r\t\u0085\u2028\u2029"):
        raise InvalidMemberValue(
            "Enter a valid value within the displayed length limit."
        )
    if field.kind is ValueKind.DATE:
        return _date(value, today)
    if field.required and not value:
        raise InvalidMemberValue("Complete this field before continuing.")
    if (
        field.choices
        and value not in field.choices
        and (
            not source.available or value != clean_text(source.value, field.max_length)
        )
    ):
        raise InvalidMemberValue("Choose one of the displayed options.")
    if field.kind is ValueKind.PHONE and value:
        return _phone(value, source)
    if field.kind is ValueKind.EMAIL and value:
        try:
            addresses = {
                normalized_email(address)
                for address in split_email_addresses(value.replace(",", ";"))
            }
        except ConfigError:
            addresses = set()
        if not addresses:
            raise InvalidMemberValue(
                "Enter valid email addresses or leave this field blank."
            )
        value = ", ".join(sorted(addresses))
        if len(value) > field.max_length:
            raise InvalidMemberValue(
                "Enter fewer email addresses within the displayed length limit."
            )
    return (
        None
        if not field.required
        and not value
        and (not source.available or source.value is None)
        else value
    )
