"""Closed census/proposal handling registry, not permission to call an API.

The public v2 contact request shapes are captured in the credential-free test
fixture. A supported field still requires value validation, current full-payload
preflight, review, tenant verification and publication admission. This module
performs no network operations.

The registry follows the live v2 write test of 2026-10-17 (#528, #1596): both
contact PUTs are partial updates, no value can be cleared (``null`` is
ignored and ``""`` refused), setting ``dateOfDeath`` leaves the Member
Active, and Family address blocks cannot be read back. So a death date, a
Family address and any answer that removes a value are worked by hand
(``proposal_handling``). The SQL proposal guard derives the same handling
(``stewardship_response_derived_guard_v1``); keep the two in step.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class Handling(StrEnum):
    """Separate potential contact writes from human and report-only work."""

    API = "api"
    MANUAL = "manual"
    REPORT_ONLY = "report-only"


@dataclass(frozen=True, slots=True)
class Capability:
    """An atomic proposal's allowed processing route and exact provider fields."""

    handling: Handling
    request_schema: str | None = None
    provider_fields: tuple[str, ...] = ()


_MEMBER_SCHEMA = "MemberContactUpdateRequestModel"
_MANUAL = Capability(Handling.MANUAL)
_REPORT = Capability(Handling.REPORT_ONLY)


FAMILY_CAPABILITIES = MappingProxyType(
    {
        # The v2 Family PUT accepts address blocks, but no v2 read returns
        # them, so a write could be neither checked for a conflict nor
        # confirmed. By hand until a verified address read exists.
        "home_address": _MANUAL,
        "mailing_address": _MANUAL,
        "email_opt_out": _MANUAL,
        "annual_pledge": _REPORT,
        "frequency": _REPORT,
        "share_methods": _REPORT,
    }
)
MEMBER_CAPABILITIES = MappingProxyType(
    {
        **{
            field: Capability(Handling.API, _MEMBER_SCHEMA, (provider,))
            for field, provider in (
                ("first_name", "firstName"),
                ("middle_name", "middleName"),
                ("last_name", "lastName"),
                ("nickname", "nickName"),
                ("maiden_name", "maidenName"),
                ("birth_date", "dateOfBirth"),
                ("language", "language"),
                ("gender", "gender"),
                ("email", "emailAddress"),
                ("home_phone", "homePhone"),
                ("mobile_phone", "cellPhone"),
                ("work_phone", "workPhone"),
            )
        },
        "prefix": _MANUAL,
        "suffix": _MANUAL,
        "marital_status": _MANUAL,
        # Writing dateOfDeath leaves the Member Active in ParishSoft, so the
        # date is entered by hand with the deceased status, never published.
        "death_date": _MANUAL,
        "deceased_status": _MANUAL,
        "moved_household": _MANUAL,
        "ministry_join": _REPORT,
        "ministry_leave": _REPORT,
    }
)
_REGISTRY = MappingProxyType(
    {
        "family": FAMILY_CAPABILITIES,
        "member": MEMBER_CAPABILITIES,
        "proposed_member": MappingProxyType(
            {"new_member": _MANUAL, "ministry_join": _REPORT}
        ),
    }
)


def capability(entity, field):
    """Reject unknown combinations rather than inventing API or manual support."""
    if type(entity) is not str or type(field) is not str:
        raise ValueError("The proposal field has no registered capability.")
    try:
        return _REGISTRY[entity][field]
    except KeyError:
        raise ValueError("The proposal field has no registered capability.") from None


def clears_value(value):
    """Whether a submitted census value removes the field's value.

    The Family form stores a removed value as ``None`` (a birth date of
    Unknown, or a blank optional field) or ``""`` (a
    blank text, email or phone over a recorded one). A proposal exists only
    when the answer differs from ParishSoft, so such an answer always clears.
    """
    return value is None or value == ""


def proposal_handling(entity, field, submitted):
    """The handling one proposal is stored with: per field, then per value.

    An API-writable field whose answer removes the value is manual, because
    the ParishSoft API cannot clear a field. The SQL guard requires exactly
    this handling on every new proposal.
    """
    handling = capability(entity, field).handling
    if handling is Handling.API and clears_value(submitted):
        return Handling.MANUAL
    return handling
