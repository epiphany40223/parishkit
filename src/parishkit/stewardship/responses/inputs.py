"""Closed inputs for the first census vertical slice, shared with form rendering.

The source adapter supplies one retained snapshot's Family-scoped records.
These are never accepted from the browser. Every displayed/prefilled value is
in the dependency projection, including unavailable fields and the complete
active household identity set. Expanding the UI requires expanding this schema;
unsupported enabled modules fail closed until their complete owners are wired.
"""

import hashlib
import json
from dataclasses import dataclass

from .census import ADDRESS_LIMITS, FAMILY_FIELDS, country_choices, us_regions
from .comparison import COMPARISON_VERSION, ValueKind, canonical_value
from .merge import KnownValue

FORM_SCHEMA = "family-census-household-v1"
PROJECTION_VERSION = "family-inputs-v2"
ADDITIONAL_MAX_LENGTH = 5000


@dataclass(frozen=True)
class CensusField:
    """One server-owned editable field, including its comparison and UI contract."""

    name: str
    source_name: str
    label: str
    kind: ValueKind
    required: bool
    max_length: int


MEMBER_FIELDS = (
    CensusField("first_name", "firstName", "First name", ValueKind.TEXT, True, 100),
    CensusField("middle_name", "middleName", "Middle name", ValueKind.TEXT, False, 100),
    CensusField("last_name", "lastName", "Last name", ValueKind.TEXT, True, 100),
    CensusField("email", "email", "Email address", ValueKind.EMAIL, False, 254),
)


class FormInputsUnavailable(ValueError):
    """No form may be issued from an incomplete or unsupported trusted input set."""


def _unavailable():
    """Do not include record values or identities in configuration/source errors."""
    raise FormInputsUnavailable("The Family form inputs are unavailable.") from None


def _digest(value):
    """Length-delimited canonical JSON prevents ambiguous concatenated identities."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class FieldInput:
    """A Family-scoped source field; raw display form is not its comparison key."""

    entity: str
    identity: int
    field: str
    kind: ValueKind
    source: KnownValue

    def comparison(self):
        """Encode known-null separately from unavailable, with type and identity."""
        return (
            self.entity,
            self.identity,
            self.field,
            self.kind.value,
            self.source.available,
            canonical_value(self.kind, self.source.value)
            if self.source.available
            else None,
        )


@dataclass(frozen=True)
class CensusInputs:
    """Answer-free trusted inputs; persistence stores only references and digests."""

    family_duid: int
    member_duids: tuple[int, ...]
    fields: tuple[FieldInput, ...]
    definition_digest: str

    @property
    def projection_digest(self):
        """Hash only this form's dependencies, not global source/config versions."""
        return _digest(
            (
                PROJECTION_VERSION,
                COMPARISON_VERSION,
                FORM_SCHEMA,
                self.family_duid,
                self.member_duids,
                self.definition_digest,
                tuple(field.comparison() for field in self.fields),
            )
        )


def definition_digest(configuration):
    """Project relevant validated campaign configuration, excluding email schedules.

    The minimal slice deliberately refuses financial/Ministry campaigns instead
    of accepting an incomplete aggregate. Their later adapters must enumerate
    offered option additions/removals as well as current selections. Content
    references are immutable, so their selected identities protect displayed
    instructions without loading unrelated admin/email configuration.
    """
    if configuration.get("modules") != ["census"]:
        _unavailable()
    content = configuration["content_versions"]
    return _digest(
        {
            "schema": FORM_SCHEMA,
            "fields": [
                (
                    field.name,
                    field.kind.value,
                    field.required,
                    field.max_length,
                    field.label,
                )
                for field in MEMBER_FIELDS
            ],
            "household_fields": [
                (field.name, field.kind.value, field.label) for field in FAMILY_FIELDS
            ],
            "address_limits": dict(ADDRESS_LIMITS),
            "country_choices": country_choices(),
            "us_regions": sorted(us_regions()),
            "name": configuration["name"],
            "start_date": configuration["start_date"],
            "end_date": configuration["end_date"],
            "timezone": configuration["timezone"],
            "additional_information": configuration["additional_information"],
            "additional_max_length": ADDITIONAL_MAX_LENGTH
            if configuration["additional_information"]
            else None,
            "content": {
                key: content[key]
                for key in ("welcome", "census", "review", "thank_you", "additional")
                if key in content
                and (key != "additional" or configuration["additional_information"])
            },
        }
    )


def member_field_value(member, contact, field):
    """Read a scoped census value consistently for forms and source reconciliation."""
    if member is None:
        return KnownValue(False)
    if contact is not None and (
        contact.get("owner_kind") != "member"
        or contact.get("owner_key") != str(member["memberDUID"])
    ):
        _unavailable()
    if field.name == "email":
        available = contact is not None and "email" in contact["available"]
        # The source normalizer splits/deduplicates addresses. Ordering is
        # presentation only, not a changed email field.
        value = (
            ", ".join(sorted(item["value"] for item in contact["emails"]))
            if available
            else None
        )
        return KnownValue(available, value)
    return KnownValue(field.source_name in member, member.get(field.source_name))


def family_field_value(field):
    """Keep unsupported source semantics unavailable rather than invent mappings.

    The verified normalized source has a primary contact address, not separate
    home/mailing addresses or their country. Its sendNoMail flag is not email
    opt-out. A future verified read adapter must change this shared projection
    and its independent SQL reconstruction together before claiming availability.
    """
    if field not in FAMILY_FIELDS:
        _unavailable()
    return KnownValue(False)


def census_inputs(family, members, contacts, *, configuration):
    """Build the complete active-household projection from trusted scoped payloads.

    Missing contacts retain explicit availability. All source Members must
    belong to this Family; foreign input is an adapter error, not silently
    usable data. Inactive/deceased Members do not appear, even if stale contacts
    remain. Raw roster, giving and other Families' payloads are not inputs.
    """
    family_duid = family.get("familyDUID")
    if (
        type(family_duid) is not int
        or not 0 < family_duid < 2**31
        or family.get("portal_eligible") is not True
    ):
        _unavailable()
    definition = definition_digest(configuration)
    fields = []
    for name in ("firstName", "lastName", "mailingName", "envelopeNumber"):
        value = family.get(name)
        # Envelope identifiers may be zero; they are read-only text, not DUIDs.
        if name == "envelopeNumber" and type(value) is int:
            value = str(value)
        fields.append(
            FieldInput(
                "family",
                family_duid,
                name,
                ValueKind.TEXT,
                KnownValue(name in family, value),
            )
        )
    active, seen = [], set()
    # Registration date is read-only but the current verified loader does not
    # supply it. Its explicit unknown still belongs to the displayed projection.
    fields.append(
        FieldInput(
            "family",
            family_duid,
            "registration_date",
            ValueKind.DATE,
            KnownValue(False),
        )
    )
    fields.extend(
        FieldInput(
            "family", family_duid, field.name, field.kind, family_field_value(field)
        )
        for field in FAMILY_FIELDS
    )
    for member in members:
        identifier = member.get("memberDUID")
        if (
            member.get("family_key") != str(family_duid)
            or type(identifier) is not int
            or not 0 < identifier < 2**31
            or identifier in seen
            or type(member.get("active")) is not bool
            or type(member.get("deceased")) is not bool
        ):
            _unavailable()
        seen.add(identifier)
        if member["active"] and not member["deceased"]:
            active.append(member)
    active.sort(key=lambda member: member["memberDUID"])
    for member in active:
        identifier = member["memberDUID"]
        contact = contacts.get(str(identifier))
        for field in MEMBER_FIELDS:
            fields.append(
                FieldInput(
                    "member",
                    identifier,
                    field.name,
                    field.kind,
                    member_field_value(member, contact, field),
                )
            )
    result = CensusInputs(
        family_duid,
        tuple(member["memberDUID"] for member in active),
        tuple(fields),
        definition,
    )
    # Detect a malformed typed source before issuing a baseline, not on Submit.
    _ = result.projection_digest
    return result
