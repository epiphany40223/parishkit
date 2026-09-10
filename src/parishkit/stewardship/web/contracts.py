"""Bounded progressive-enhancement inputs and safe machine-readable failures."""

import re
from dataclasses import dataclass
from enum import StrEnum

from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.storage import StaleRecordError


class ErrorCode(StrEnum):
    INVALID = "invalid"
    REQUIRED = "required"
    STALE = "stale_version"
    UNAVAILABLE = "unavailable"
    DENIED = "denied"


MESSAGES = {
    ErrorCode.INVALID: _("Check this value."),
    ErrorCode.REQUIRED: _("Enter a value."),
    ErrorCode.STALE: _("This information changed. Reload before trying again."),
    ErrorCode.UNAVAILABLE: _(
        "This information is temporarily unavailable. Retry later."
    ),
    ErrorCode.DENIED: _("Access is unavailable. Sign in again."),
}


@dataclass(frozen=True)
class FieldError:
    """Only server-owned field IDs and closed error codes cross this boundary."""

    code: ErrorCode
    field_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.code, ErrorCode) or (
            self.field_id is not None
            and (
                type(self.field_id) is not str
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.field_id) is None
            )
        ):
            raise ValueError("Validation requires a canonical code and safe field ID.")

    def as_dict(self):
        """Messages never include the submitted value or an exception string."""
        return {
            "code": self.code.value,
            "field_id": self.field_id,
            "message": str(MESSAGES[self.code]),
        }


def validation_response(errors, *, status=400):
    """Return safe JSON to enhanced clients; HTML views own their error rendering."""
    if status not in {400, 403, 409, 422, 503}:
        raise ValueError("Unsupported validation status.")
    if (
        type(errors) not in {tuple, list}
        or not 1 <= len(errors) <= 100
        or any(not isinstance(item, FieldError) for item in errors)
    ):
        raise ValueError("Validation errors must be bounded typed records.")
    response = JsonResponse(
        {"errors": [item.as_dict() for item in errors]}, status=status
    )
    response.stewardship_safe_error = True
    response["Cache-Control"] = "no-store"
    if status == 503:
        response["Retry-After"] = "5"
    return response


def expected_version(value):
    """Parse a bounded, canonical decimal version without trusting browser types."""
    if type(value) is not str or re.fullmatch(r"[1-9][0-9]{0,18}", value) is None:
        raise ValueError("A positive expected version is required.")
    parsed = int(value)
    if parsed > 2**63 - 1:
        raise ValueError("Expected version exceeds the supported bound.")
    return parsed


def check_version(record, expected):
    """Call after acquiring the owner's row lock, never as optimistic authority."""
    if type(expected) is not int or not 1 <= expected <= 2**63 - 1:
        raise ValueError("Expected version must be a positive integer.")
    if record.version != expected:
        raise StaleRecordError(str(MESSAGES[ErrorCode.STALE]))


@dataclass(frozen=True)
class PageWindow:
    """Server-side bounded pages; query/filter values are never report audit data."""

    page: int = 1
    size: int = 50

    def __post_init__(self):
        if (
            type(self.page) is not int
            or not 1 <= self.page <= 10000
            or type(self.size) is not int
            or not 1 <= self.size <= 100
        ):
            raise ValueError("Page and size exceed their supported bounds.")

    def rows(self, query):
        """Fetch one sentinel row for has-next instead of an unbounded total scan."""
        start = (self.page - 1) * self.size
        rows = list(query[start : start + self.size + 1])
        return rows[: self.size], len(rows) > self.size


def filters(parameters, *, allowed):
    """Reject repeated/unrecognized keys and bound searches before any ORM use."""
    if set(parameters) - set(allowed):
        raise ValueError("Unknown filter input.")
    result = {}
    for key in parameters:
        values = parameters.getlist(key)
        if len(values) != 1 or len(values[0]) > 200:
            raise ValueError("Filter inputs must be single bounded values.")
        result[key] = values[0]
    return result
