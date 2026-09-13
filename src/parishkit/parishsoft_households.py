"""Complete bounded Family/Member reads for coherent source delta consumers."""

from dataclasses import dataclass

from .parishsoft import ParishSoftAPIError
from .parishsoft_pagination import IncompleteSourceCollection
from .parishsoft_source import CoherentParishSoftClient


@dataclass(frozen=True)
class FamilySourceSlice:
    """Primary search DTOs and compatible contact fallbacks for one exact Family."""

    family: dict
    members: dict
    contacts: dict


def _identity(value):
    """Provider identity values are exact positive int32, never coerced aliases."""
    if type(value) is not int or not 1 <= value < 2**31:
        raise IncompleteSourceCollection("Family slice identity is invalid.")
    return value


def _read(client, endpoint):
    """An unavailable scoped endpoint requires a full source refresh instead."""
    try:
        return client.get_uncached(endpoint)
    except ParishSoftAPIError as error:
        if error.status_code in {400, 404, 405, 501}:
            raise IncompleteSourceCollection(
                "The Family slice is unavailable."
            ) from None
        raise


def load_family_slice(client, *, family_id, maximum_members=200):
    """Read the whole household plus each Member's search-equivalent census DTO.

    The unpaginated Family Member list enumerates identities and supplies the
    contact fallback fields missing from Member search. Its ceiling is a safety
    refusal, not truncation. A caller must compare household composition with
    retained truth before replacing a delta, because moved/deleted Members can
    affect other Families and rosters beyond this query's scope.
    """
    _identity(family_id)
    if (
        not isinstance(client, CoherentParishSoftClient)
        or type(maximum_members) is not int
        or not 1 <= maximum_members <= 1000
    ):
        raise ValueError("A coherent source client and bounded household are required.")
    client._guard()
    family = _read(client, f"families/{family_id}")
    if type(family) is not dict or _identity(family.get("familyDUID")) != family_id:
        raise IncompleteSourceCollection("Family slice returned another identity.")
    rows = _read(client, f"families/{family_id}/member/list")
    if type(rows) is not list or len(rows) >= maximum_members:
        raise IncompleteSourceCollection("Family slice membership is incomplete.")
    details = {}
    for row in rows:
        if type(row) is not dict or _identity(row.get("familyDUID")) != family_id:
            raise IncompleteSourceCollection(
                "Family slice has an ambiguous relationship."
            )
        identifier = _identity(row.get("memberDUID"))
        if identifier in details:
            raise IncompleteSourceCollection("Family slice repeats a Member identity.")
        details[identifier] = row
    members, contacts = {}, {}
    for identifier, detail in sorted(details.items()):
        member = _read(client, f"members/{identifier}")
        if (
            type(member) is not dict
            or _identity(member.get("memberDUID")) != identifier
            or _identity(member.get("familyDUID")) != family_id
        ):
            raise IncompleteSourceCollection("Family slice Member ownership changed.")
        members[identifier] = member
        # No SSNs, sacramental data or unrelated Family-detail fields are copied.
        contacts[identifier] = {
            "memberDUID": identifier,
            **{
                target: detail[source]
                for target, source in (
                    ("middleName", "middleName"),
                    ("nickName", "nickName"),
                    ("maidenName", "maidenName"),
                    ("dateOfBirth", "birthdate"),
                    ("gender", "sex"),
                    ("emailAddress", "emailAddress"),
                    ("homePhone", "homePhone"),
                    ("cellPhone", "cellPhone"),
                    ("workPhone", "workPhone"),
                )
                if source in detail
            },
        }
    return FamilySourceSlice(family, members, contacts)
