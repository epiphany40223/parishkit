"""Pure household census values, independent of storage and browser drafts.

Country selection uses the pinned ISO dataset. Address validation is syntactic,
not a promise of postal deliverability. Missing upstream values remain unknown;
neither an empty address nor an untouched opt-out control invents source truth.
"""

import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from types import MappingProxyType

import pycountry

from .comparison import ADDRESS_COMPONENTS, ValueKind, canonical_value
from .merge import KnownValue

ADDRESS_LIMITS = MappingProxyType(
    {
        "line1": 200,
        "line2": 200,
        "city": 100,
        "region": 100,
        "postal_code": 32,
        "country": 2,
    }
)


@dataclass(frozen=True)
class HouseholdField:
    """One closed editable household field, shared by every response consumer."""

    name: str
    label: str
    kind: ValueKind


FAMILY_FIELDS = (
    HouseholdField("home_address", "Home address", ValueKind.ADDRESS),
    HouseholdField("mailing_address", "Mailing address", ValueKind.ADDRESS),
    HouseholdField("email_opt_out", "Opt out of all parish emails", ValueKind.BOOLEAN),
)
HOUSEHOLD_FIELDS = frozenset(field.name for field in FAMILY_FIELDS)


class InvalidHousehold(ValueError):
    """Static, server-known field errors without private input in the exception."""

    def __init__(self, fields):
        """Retain only validation messages for the owning form's error mapping."""
        self.fields = dict(fields)
        super().__init__("Please review the indicated household census fields.")


def clean_text(value, limit):
    """Normalize bounded text while rejecting controls and Unicode surrogates."""
    if (
        type(value) is not str
        or len(value) > limit
        or any(
            (ord(character) < 32 and character not in "\n\r\t")
            or 0xD800 <= ord(character) <= 0xDFFF
            or ord(character) == 127
            for character in value
        )
    ):
        return None
    normalized = unicodedata.normalize("NFC", value).strip()
    return normalized if len(normalized) <= limit else None


@cache
def country_choices():
    """Return immutable country code/label pairs in deterministic English order."""
    return tuple(
        sorted(
            ((country.alpha_2, country.name) for country in pycountry.countries),
            key=lambda item: (item[1].casefold(), item[0]),
        )
    )


@cache
def us_regions():
    """Include states, district, territories and USPS military routing regions."""
    return frozenset(
        subdivision.code.removeprefix("US-")
        for subdivision in pycountry.subdivisions.get(country_code="US")
    ) | {"AA", "AE", "AP"}


def blank_address():
    """Provide a fresh empty browser representation, never a fabricated source."""
    return dict.fromkeys(ADDRESS_COMPONENTS, "")


def validate_address(payload):
    """Accept a complete blank or country-aware structured address.

    Nonblank addresses require a street/delivery line, city/locality and explicit
    country. Region and postal code are optional internationally; only US
    addresses require a recognized region and five-digit or ZIP+4 syntax.
    Other postal formats remain bounded text, including countries with no code.
    """
    if type(payload) is not dict or set(payload) != set(ADDRESS_COMPONENTS):
        raise InvalidHousehold({"address": "Review every address field."})
    errors, result = {}, {}
    for component, limit in ADDRESS_LIMITS.items():
        value = clean_text(payload[component], limit)
        if value is None or any(
            character in value for character in "\n\r\t\u0085\u2028\u2029"
        ):
            errors[component] = "Enter a single line within the displayed length limit."
        else:
            result[component] = value
    if errors:
        raise InvalidHousehold(errors)
    if not any(result.values()):
        return None
    for component in ("line1", "city", "country"):
        if not result[component]:
            errors[component] = "Complete this field for the address."
    result["country"] = result["country"].upper()
    if result["country"] not in dict(country_choices()):
        errors["country"] = "Select a country from the displayed list."
    if result["country"] == "US":
        result["region"] = result["region"].upper()
        if result["region"] not in us_regions():
            errors["region"] = "Enter a valid US state or territory abbreviation."
        if re.fullmatch(r"[0-9]{5}(?:-[0-9]{4})?", result["postal_code"]) is None:
            errors["postal_code"] = "Enter a five-digit ZIP code or ZIP+4."
    if errors:
        raise InvalidHousehold(errors)
    return result


def validate_household(payload, sources):
    """Validate the complete household answer with trusted source availability.

    None on an opt-out control means an untouched unavailable source value, not
    False. An explicit boolean expresses the Family's choice. Same-as-home is
    a UI convenience captured in the answer; both actual addresses must still
    be supplied and agree, so Submit never silently copies incomplete input.
    """
    if (
        type(sources) is not dict
        or set(sources) != HOUSEHOLD_FIELDS
        or any(not isinstance(value, KnownValue) for value in sources.values())
    ):
        raise TypeError("Trusted household source availability is required.")
    if type(payload) is not dict or set(payload) != (
        HOUSEHOLD_FIELDS | {"mailing_same_as_home"}
    ):
        raise InvalidHousehold({"family": "Review every household census field."})
    errors, result = {}, {}
    for field in ("home_address", "mailing_address"):
        try:
            result[field] = validate_address(payload[field])
        except InvalidHousehold as error:
            errors.update(
                {f"{field}.{key}": value for key, value in error.fields.items()}
            )
    same = payload["mailing_same_as_home"]
    if type(same) is not bool:
        errors["mailing_same_as_home"] = "Review the mailing-address choice."
    elif (
        same
        and all(field in result for field in ("home_address", "mailing_address"))
        and (
            result["home_address"] is None
            or canonical_value(ValueKind.ADDRESS, result["home_address"])
            != canonical_value(ValueKind.ADDRESS, result["mailing_address"])
        )
    ):
        errors["mailing_same_as_home"] = "Complete matching home and mailing addresses."
    result["mailing_same_as_home"] = same
    opt_out, source = payload["email_opt_out"], sources["email_opt_out"]
    if type(opt_out) is not bool and not (
        opt_out is None and (not source.available or source.value is None)
    ):
        errors["email_opt_out"] = "Review the parish email preference."
    result["email_opt_out"] = opt_out
    if errors:
        raise InvalidHousehold(errors)
    return result
