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

import phonenumbers

from parishkit.stewardship.audit.schemas import ContextKind, sanitize

from .census import ADDRESS_LIMITS, FAMILY_FIELDS, country_choices, us_regions
from .comparison import COMPARISON_VERSION, ValueKind, canonical_value
from .financial import FREQUENCIES, MAX_PLEDGE_CENTS, SHARE_TEXT_LIMIT
from .financial_inputs import FinancialInputs
from .member_census import MEMBER_FIELDS, InvalidMemberSource, source_value
from .member_requests import MAX_PROPOSED_MEMBERS, REQUEST_FIELDS
from .merge import KnownValue
from .ministry import MAX_MINISTRY_LABEL, MinistryInputs

FORM_SCHEMA = "family-response-v1"
PROJECTION_VERSION = "family-inputs-v7"
ADDITIONAL_MAX_LENGTH = 5000


class FormInputsUnavailable(ValueError):
    """No form may be issued from an incomplete or unsupported trusted input set."""


class MemberSourceUnavailable(FormInputsUnavailable):
    """A scoped Member field is unrepresentable, not an ownership/adapter fault."""

    def __init__(self, *, family_duid, member_duid, field):
        """Carry only validated source identifiers, never the unusable value."""
        self.context = sanitize(
            ContextKind.MEMBER_SOURCE,
            {"family_duid": family_duid, "member_duid": member_duid, "field": field},
        )
        super().__init__("The Family form inputs are unavailable.")


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
    identity: int | str
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
    modules: tuple[str, ...] = ("census",)
    ministries: MinistryInputs | None = None
    financial: FinancialInputs | None = None
    parish_name: str = ""

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
                self.modules,
                self.ministries.comparison() if self.ministries is not None else None,
                self.financial.comparison() if self.financial is not None else None,
                self.parish_name if self.financial is not None else None,
                tuple(field.comparison() for field in self.fields),
            )
        )


def definition_digest(configuration):
    """Project relevant validated campaign configuration, excluding email schedules.

    Ministry adapters enumerate visible option additions/removals and current
    memberships independently of unrelated campaign settings. Content
    references are immutable, so their selected identities protect displayed
    instructions without loading unrelated admin/email configuration.
    """
    modules = configuration.get("modules")
    if (
        type(modules) is not list
        or not modules
        or any(
            type(value) is not str or value not in {"census", "ministry", "financial"}
            for value in modules
        )
        or len(modules) != len(set(modules))
    ):
        _unavailable()
    census = "census" in modules
    content = configuration["content_versions"]
    return _digest(
        {
            "schema": FORM_SCHEMA,
            "modules": sorted(modules),
            "financial": {
                "periods": configuration.get("financial"),
                "year_label": configuration.get("year_label"),
                "options": configuration.get("share_options"),
                "max_pledge_cents": MAX_PLEDGE_CENTS,
                "share_text_limit": SHARE_TEXT_LIMIT,
                "frequencies": FREQUENCIES,
            }
            if "financial" in modules
            else None,
            "ministry_duids": sorted(configuration["ministry_duids"])
            if "ministry" in modules
            else [],
            "ministry_label_limit": MAX_MINISTRY_LABEL
            if "ministry" in modules
            else None,
            "max_proposed_members": MAX_PROPOSED_MEMBERS if census else 0,
            "request_fields": [
                (field.name, field.kind.value) for field in REQUEST_FIELDS if census
            ],
            "fields": [
                (
                    field.name,
                    field.kind.value,
                    field.required,
                    field.max_length,
                    field.label,
                    field.choices,
                )
                for field in MEMBER_FIELDS
                if census
            ],
            "household_fields": [
                (field.name, field.kind.value, field.label)
                for field in FAMILY_FIELDS
                if census
            ],
            "phone_metadata": phonenumbers.__version__ if census else None,
            "phone_national_region": "US" if census else None,
            "address_limits": dict(ADDRESS_LIMITS) if census else None,
            "country_choices": country_choices() if census else None,
            "us_regions": sorted(us_regions()) if census else None,
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
                for key in (
                    "welcome",
                    "census",
                    "ministry",
                    "financial",
                    "review",
                    "thank_you",
                    "additional",
                )
                if key in content
                and (key != "additional" or configuration["additional_information"])
                and (key not in {"census", "ministry", "financial"} or key in modules)
            },
        }
    )


def member_field_value(member, contact, field):
    """Translate malformed retained source to the endpoint's safe unavailable path."""
    try:
        return _member_field_value(member, contact, field)
    except InvalidMemberSource:
        raise MemberSourceUnavailable(
            family_duid=int(member["family_key"]),
            member_duid=member["memberDUID"],
            field=field.name,
        ) from None


def _member_field_value(member, contact, field):
    """Read a scoped census value consistently for forms and source reconciliation."""
    if member is None:
        return KnownValue(False)
    if field.name in {"moved_household", "deceased_status"}:
        # A scoped active Member is neither moved nor deceased. These semantic
        # inputs are separate from dateOfDeath and cannot be inferred from it.
        return KnownValue(True, False)
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
        return KnownValue(available, source_value(field, value))
    if field.kind is ValueKind.PHONE:
        available = contact is not None and field.source_name in contact["available"]
        return KnownValue(
            available,
            source_value(field, contact["phones"].get(field.source_name))
            if available
            else None,
        )
    return KnownValue(
        field.source_name in member, source_value(field, member.get(field.source_name))
    )


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


def census_inputs(
    family,
    members,
    contacts,
    *,
    configuration,
    ministries=None,
    financial=None,
    parish_name="",
):
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
    census = "census" in configuration["modules"]
    fields = []
    for name in ("firstName", "lastName", "mailingName", "envelopeNumber"):
        if name == "envelopeNumber" and not census:
            continue
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
    if census:
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
        fields.append(
            FieldInput(
                "member_context",
                identifier,
                "relationship",
                ValueKind.TEXT,
                KnownValue("memberType" in member, member.get("memberType")),
            )
        )
        for field in (*MEMBER_FIELDS, *REQUEST_FIELDS) if census else ():
            fields.append(
                FieldInput(
                    "member",
                    identifier,
                    field.name,
                    field.kind,
                    member_field_value(member, contact, field),
                )
            )
        if not census:
            # Ministry-only screens identify the Member but never load or
            # validate unrelated birth, gender, email or phone census fields.
            for field in MEMBER_FIELDS:
                if field.name in {"first_name", "last_name"}:
                    fields.append(
                        FieldInput(
                            "member_context",
                            identifier,
                            field.name,
                            field.kind,
                            member_field_value(member, None, field),
                        )
                    )
    identifiers = tuple(member["memberDUID"] for member in active)
    if "ministry" in configuration["modules"]:
        if (
            not isinstance(ministries, MinistryInputs)
            or tuple(member for member, _ in ministries.memberships) != identifiers
        ):
            _unavailable()
    elif ministries is not None:
        _unavailable()
    if "financial" in configuration["modules"]:
        if (
            not isinstance(financial, FinancialInputs)
            or financial.family_duid != family_duid
        ):
            _unavailable()
    elif financial is not None:
        _unavailable()
    result = CensusInputs(
        family_duid,
        identifiers,
        tuple(fields),
        definition,
        tuple(sorted(configuration["modules"])),
        ministries,
        financial,
        parish_name,
    )
    # Detect a malformed typed source before issuing a baseline, not on Submit.
    _ = result.projection_digest
    return result
