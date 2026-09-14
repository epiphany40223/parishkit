"""Closed terminal requests and local proposed Members, without persistence.

Terminal input may still contain disabled ordinary controls. Those values are
deliberately not validated, retained, or used for death/birth ordering. Only a
trusted recorded birth date participates in that comparison. New household
Members have local UUIDs, never guessed provider identities.
"""

import re
from datetime import date
from uuid import UUID

from .comparison import ValueKind
from .member_census import (
    MEMBER_FIELDS,
    CensusField,
    InvalidMemberValue,
    validate_member_value,
)
from .merge import KnownValue

MAX_PROPOSED_MEMBERS = 100
REQUEST_FIELDS = (
    CensusField(
        "moved_household", "", "No longer in this household", ValueKind.BOOLEAN
    ),
    CensusField("deceased_status", "deceased", "Deceased", ValueKind.BOOLEAN),
    CensusField(
        "death_date", "dateOfDeath", "Death date", ValueKind.DATE, max_length=10
    ),
)
ORDINARY_NAMES = frozenset(field.name for field in MEMBER_FIELDS)
TERMINAL_NAMES = frozenset(
    {"moved_household", "deceased_status", "death_date", "confirmed"}
)


class InvalidMemberAnswers(ValueError):
    """Static errors with closed field names, excluding submitted private text."""

    def __init__(self, fields):
        """Retain only owner-produced field explanations for accessible errors."""
        self.fields = fields
        super().__init__("Review this household member's fields.")


def local_member_id(value):
    """Accept canonical nonzero UUID text without normalizing colliding keys."""
    if type(value) is not str:
        return False
    try:
        parsed = UUID(value)
        return bool(parsed.int) and str(parsed) == value
    except ValueError:
        return False


def ordinary_answers(raw, sources, *, today):
    """Normalize all ordinary controls, including explicit Unknown birth date."""
    if type(raw) is not dict or set(raw) != ORDINARY_NAMES:
        raise InvalidMemberAnswers(
            {"": "Review every field for this household member."}
        )
    normalized, errors = {}, {}
    for field in MEMBER_FIELDS:
        try:
            normalized[field.name] = validate_member_value(
                field,
                raw[field.name],
                sources.get(field.name, KnownValue(False)),
                today=today,
            )
        except InvalidMemberValue as error:
            errors[field.name] = str(error)
    if errors:
        raise InvalidMemberAnswers(errors)
    return normalized


def existing_answers(raw, sources, *, today):
    """Confirm one terminal request or validate the complete ordinary Member."""
    if type(raw) is not dict or not (set(raw) & TERMINAL_NAMES):
        return ordinary_answers(raw, sources, today=today)
    moved = raw.get("moved_household") is True
    deceased = raw.get("deceased_status") is True
    expected = (
        {"confirmed", "moved_household"}
        if moved
        else {"confirmed", "deceased_status", "death_date"}
    )
    if (
        moved == deceased
        or not expected <= set(raw)
        or set(raw) - ORDINARY_NAMES - expected
        or raw.get("confirmed") is not True
    ):
        raise InvalidMemberAnswers(
            {"confirmed": "Confirm exactly one household change."}
        )
    if moved:
        return {"moved_household": True, "confirmed": True}
    value = raw["death_date"]
    parsed = None
    try:
        if type(value) is not str:
            raise ValueError
        if value:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError
            parsed = date.fromisoformat(value)
            birth = sources.get("birth_date", KnownValue(False))
            if parsed > today or (
                birth.available
                and birth.value
                and parsed < date.fromisoformat(birth.value)
            ):
                raise ValueError
    except ValueError:
        raise InvalidMemberAnswers(
            {
                "death_date": (
                    "Enter a valid death date, not before birth or in the future, "
                    "or leave blank."
                )
            }
        ) from None
    return {
        "deceased_status": True,
        "death_date": parsed.isoformat() if parsed else None,
        "confirmed": True,
    }
