"""Closed census/proposal handling registry, not permission to call an API.

The public v2 contact request shapes are captured in the credential-free test
fixture. A supported field still requires value validation, current full-payload
preflight, review, tenant verification and publication admission. Those external
write workflows belong to Phase 6; this module performs no network operations.
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


_FAMILY_SCHEMA = "FamilyContactUpdateRequestModel"
_MEMBER_SCHEMA = "MemberContactUpdateRequestModel"
_MANUAL = Capability(Handling.MANUAL)
_REPORT = Capability(Handling.REPORT_ONLY)


def _address(prefix):
    """An address proposal is one structured value mapped to its full component set."""
    return Capability(
        Handling.API,
        _FAMILY_SCHEMA,
        tuple(
            prefix + suffix
            for suffix in (
                "AddressLine1",
                "AddressLine2",
                "City",
                "State",
                "Country",
                "PostalCode",
                "PostalCodePlus4",
            )
        ),
    )


FAMILY_CAPABILITIES = MappingProxyType(
    {
        "home_address": _address("home"),
        "mailing_address": _address("mailing"),
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
                ("death_date", "dateOfDeath"),
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
