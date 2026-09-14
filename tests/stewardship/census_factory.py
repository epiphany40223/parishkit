"""Synthetic complete household values shared by pure, HTTP and browser tests."""

from parishkit.stewardship.responses.census import HOUSEHOLD_FIELDS, blank_address
from parishkit.stewardship.responses.member_census import MEMBER_FIELDS
from parishkit.stewardship.responses.merge import KnownValue


def member(**changes):
    """Complete synthetic browser fields, including explicit required choices."""
    return {field.name: "" for field in MEMBER_FIELDS} | {
        "first_name": "Alex",
        "last_name": "Sample",
        "birth_date": "1960-01-01",
        "gender": "Unspecified",
        "language": "English",
        **changes,
    }


def address(**changes):
    """An invented mailing address, never a claim of an actual deliverable location."""
    return {
        "line1": "123 Sample Street",
        "line2": "",
        "city": "Louisville",
        "region": "KY",
        "postal_code": "40223",
        "country": "US",
        **changes,
    }


def household(**changes):
    """An untouched browser answer represents every currently unavailable field."""
    return {
        "home_address": blank_address(),
        "mailing_address": blank_address(),
        "mailing_same_as_home": False,
        "email_opt_out": None,
        **changes,
    }


def sources(**changes):
    """Only the trusted source owner supplies availability, never the browser."""
    return {field: KnownValue(False) for field in HOUSEHOLD_FIELDS} | changes
