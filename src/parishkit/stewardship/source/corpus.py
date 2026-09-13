"""Normalize the shared Family/Member corpus without copying its cyclic links.

This layer preserves only campaign-relevant provider fields, excluding SSNs,
unrelated sensitive detail, pagination counters and volatile modification
dates. Contacts and primary addresses have independent content identities.
Scoped giving has a separate owner; this core converter never loads history.
"""

import hashlib
import json
import unicodedata
from datetime import date, datetime

from parishkit.config import ConfigError
from parishkit.parishsoft import (
    ParishSoftData,
    family_is_active,
    family_is_parishioner,
    get_family_heads,
    member_is_active,
    member_is_deceased,
    ministry_membership_is_current,
    split_email_addresses,
)
from parishkit.stewardship.accounts.policy_schema import normalized_email

from .canonical import InvalidSourcePayload, canonical_payload

KINDS = (
    "family",
    "member",
    "contact",
    "address",
    "ministry",
    "roster",
    "fund",
    "pledge",
    "contribution",
)
FAMILY_FIELDS = (
    "familyDUID",
    "familyID",
    "firstName",
    "lastName",
    "mailingName",
    "envelopeNumber",
    "registeredOrganizationID",
    "famGroupID",
    "status",
    "familyParticipationStatus",
    "hasMembers",
    "hasSuspense",
    "sendNoMail",
)
MEMBER_FIELDS = (
    "memberDUID",
    "firstName",
    "middleName",
    "lastName",
    "nickName",
    "salutation",
    "suffix",
    "maidenName",
    "memberType",
    "memberStatus",
    "sex",
    "maritalStatus",
    "maritalStatusID",
    "language",
    "birthdate",
    "dateOfDeath",
)
MEMBER_CONTACT_FALLBACKS = {
    "middleName": "middleName",
    "nickName": "nickName",
    "maidenName": "maidenName",
    "birthdate": "dateOfBirth",
    "dateOfDeath": "dateOfDeath",
    "sex": "gender",
}
ADDRESS_FIELDS = (
    "primaryAddress1",
    "primaryAddress2",
    "primaryAddress3",
    "primaryCity",
    "primaryState",
    "primaryPostalCode",
    "primaryZipPlus",
)
INTEGER_FIELDS = frozenset(
    {
        "familyDUID",
        "familyID",
        "registeredOrganizationID",
        "famGroupID",
        "envelopeNumber",
        "hasMembers",
        "memberDUID",
        "maritalStatusID",
        "id",
        "fundId",
        "ministryRoleId",
        "ministryEventId",
    }
)
BOOLEAN_FIELDS = frozenset(
    {
        "status",
        "hasSuspense",
        "sendNoMail",
        "active",
        "requiresPledges",
        "trained",
        "subOnly",
    }
)
DATE_FIELDS = frozenset({"birthdate", "dateOfDeath", "fundStartDate", "fundEndDate"})


def _id(value):
    """Source identities are exact positive provider integers, never guessed aliases."""
    if type(value) is not int or not 1 <= value < 2**31:
        raise InvalidSourcePayload("Source identity is invalid.")
    return value


def _scalar(value):
    """Keep scalars bounded and canonical without echoing private values."""
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is str and len(value) <= 8192:
        return unicodedata.normalize("NFC", value.strip())
    if isinstance(value, date):
        return _date(value)
    raise InvalidSourcePayload("Source field has an unsupported type or length.")


def _date(value):
    """Provider census/roster dates are civil dates, not guessed UTC timestamps."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if type(value) is date:
            return value.isoformat()
        if type(value) is str and len(value) <= 40:
            return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        pass
    raise InvalidSourcePayload("Source civil date is invalid.")


def _fields(row, names):
    """A closed field vocabulary prevents accidental retention of extra provider PII."""
    values = {}
    for key in names:
        if key not in row:
            continue
        value = row[key]
        if key in DATE_FIELDS:
            values[key] = _date(value)
            continue
        expected = (
            int if key in INTEGER_FIELDS else bool if key in BOOLEAN_FIELDS else str
        )
        if value is not None and type(value) is not expected:
            raise InvalidSourcePayload("Source field type differs from its contract.")
        if expected is int and value is not None and not -(2**31) <= value < 2**31:
            raise InvalidSourcePayload("Source integer is out of range.")
        values[key] = _scalar(value)
    return values


def _entries(rows, identity):
    """Dictionary indexing must not have changed a raw record's exact identity."""
    if type(rows) is not dict:
        raise InvalidSourcePayload("Source collection is not an identity mapping.")
    for key, row in rows.items():
        if type(row) is not dict or _id(key) != _id(row.get(identity)):
            raise InvalidSourcePayload("Source record identity differs from its index.")
    return sorted(rows.items())


def _put(corpus, kind, key, payload):
    """Validate each independent immutable payload before any database staging."""
    if key in corpus[kind]:
        raise InvalidSourcePayload("Source collection repeats a normalized identity.")
    canonical, _ = canonical_payload({"schema_version": 1, **payload})
    corpus[kind][key] = json.loads(canonical)


def _contact(
    corpus, kind, identifier, *, email, phones, available, publish_email, publish_phone
):
    """Retain invalid source contact text for correction, not for addressing mail."""
    if email is not None and type(email) is not str:
        raise InvalidSourcePayload("Source email field is not text.")
    values = {}
    for fragment in sorted(split_email_addresses(_scalar(email) or "")):
        try:
            value = normalized_email(fragment)
            valid = True
        except ConfigError:
            value, valid = fragment, False
        values[value] = {"value": value, "valid": valid}
    values = [values[key] for key in sorted(values)]
    cleaned_phones = {}
    for name, value in phones.items():
        if value is not None and type(value) is not str:
            raise InvalidSourcePayload("Source phone field is not text.")
        if value and value.strip():
            cleaned_phones[name] = _scalar(value)
    for publish in (publish_email, publish_phone):
        if publish is not None and type(publish) is not bool:
            raise InvalidSourcePayload("Source contact publication flag is invalid.")
    if available:
        _put(
            corpus,
            "contact",
            f"{kind}:{identifier}",
            {
                "owner_kind": kind,
                "owner_key": str(identifier),
                "emails": values,
                "phones": cleaned_phones,
                "available": sorted(available),
                "publish_email": publish_email is True,
                "publish_phone": publish_phone is True,
            },
        )
    return any(value["valid"] for value in values)


def _address(corpus, kind, identifier, fields, publishable):
    """Retain a known primary address without inventing home/mailing equivalence.

    Neither v1 nor v2 Family search returns separate home/mailing read fields or
    a country/registration date. Preserve availability faithfully; later census
    field mapping cannot treat unavailable fields as verified blank source data.
    """
    values = _fields(fields, ADDRESS_FIELDS)
    if publishable is not None and type(publishable) is not bool:
        raise InvalidSourcePayload("Source address publication flag is invalid.")
    if values:
        _put(
            corpus,
            "address",
            f"{kind}:{identifier}:primary",
            {
                "owner_kind": kind,
                "owner_key": str(identifier),
                "kind": "primary",
                "fields": values,
                "publishable": publishable is True,
            },
        )


def normalize_core(source, *, as_of):
    """Build all explicit core collections, retaining inactive/empty source Families.

    Call the shared full loader with active_only=False, parishioners_only=False,
    include_deceased=True and retain_empty_families=True. Funds may be attached
    from the separately loaded catalog. Giving must be loaded/normalized by its
    explicit current-window owner, never through an unscoped history option.
    """
    if not isinstance(source, ParishSoftData) or type(as_of) is not date:
        raise InvalidSourcePayload(
            "A shared source corpus and parish civil date are required."
        )
    _id(source.organization_id)
    if source.pledges or source.contributions:
        raise InvalidSourcePayload("Unscoped giving cannot be normalized as core data.")
    corpus = {kind: {} for kind in KINDS}
    family_rows = _entries(source.families, "familyDUID")
    grouped = {identifier: [] for identifier, _ in family_rows}
    member_emails = {}
    for identifier, raw in _entries(source.members, "memberDUID"):
        family_id = _id(raw.get("familyDUID"))
        if family_id not in grouped:
            raise InvalidSourcePayload("Source Member has no retained Family.")
        contact = source.member_contactinfos.get(identifier, {})
        if type(contact) is not dict:
            raise InvalidSourcePayload("Source Member contact information is invalid.")
        values = _fields(raw, MEMBER_FIELDS)
        for field, fallback in MEMBER_CONTACT_FALLBACKS.items():
            if field not in values and fallback in contact:
                values.update(_fields({field: contact[fallback]}, (field,)))
        for field in ("birthdate", "dateOfDeath"):
            if field in values:
                values[field] = _date(values[field])
        values.update(
            family_key=str(family_id),
            active=member_is_active(values),
            deceased=member_is_deceased(values),
        )
        _put(corpus, "member", str(identifier), values)
        grouped[family_id].append(values)
        member_emails[identifier] = _contact(
            corpus,
            "member",
            identifier,
            email=raw.get("emailAddress", contact.get("emailAddress")),
            phones={
                "home": raw.get("homePhone", contact.get("homePhone")),
                "mobile": raw.get("mobilePhone", contact.get("cellPhone")),
                "work": raw.get("workPhone", contact.get("workPhone")),
            },
            available={
                name
                for name, primary, fallback in (
                    ("email", "emailAddress", "emailAddress"),
                    ("home", "homePhone", "homePhone"),
                    ("mobile", "mobilePhone", "cellPhone"),
                    ("work", "workPhone", "workPhone"),
                )
                if primary in raw or fallback in contact
            },
            publish_email=raw.get("family_PublishEMail"),
            publish_phone=raw.get("family_PublishPhone"),
        )
    for identifier, raw in family_rows:
        values = _fields(raw, FAMILY_FIELDS)
        for field in ("familyID", "registeredOrganizationID", "famGroupID"):
            value = values.get(field)
            if value is not None and (type(value) is not int or not 0 <= value < 2**31):
                raise InvalidSourcePayload(
                    "Source Family reference metadata is invalid."
                )
        group_id = raw.get("famGroupID")
        if group_id and group_id not in source.family_groups:
            raise InvalidSourcePayload("Source Family group reference is unavailable.")
        group = source.family_groups.get(group_id)
        if group is not None and type(group) is not str:
            raise InvalidSourcePayload("Source Family group is not text.")
        group = _scalar(group)
        family = {**values, "py members": grouped[identifier], "py family group": group}
        heads = [
            key
            for key, member in get_family_heads(family).items()
            if member_is_active(member)
        ]
        active = family_is_active(family)
        registered = family_is_parishioner(family, source.organization_id)
        _put(
            corpus,
            "family",
            str(identifier),
            {
                **values,
                "family_group": group,
                "active": active,
                "parishioner": registered,
                "portal_eligible": active and registered,
                "active_head_duids": sorted(heads),
                "email_eligible": active
                and registered
                and any(member_emails[key] for key in heads),
            },
        )
        _contact(
            corpus,
            "family",
            identifier,
            email=raw.get("eMailAddress"),
            phones={
                "home": raw.get("familyHomePhone"),
                "primary": raw.get("primaryPhone"),
            },
            available={
                name
                for name, field in (
                    ("email", "eMailAddress"),
                    ("home", "familyHomePhone"),
                    ("primary", "primaryPhone"),
                )
                if field in raw
            },
            publish_email=raw.get("primaryPublishEMail"),
            publish_phone=raw.get("primaryPublishPhone"),
        )
        _address(corpus, "family", identifier, raw, raw.get("primaryPublishAddress"))
    _ministries(corpus, source, as_of)
    for identifier, fund in _entries(source.funds, "fundId"):
        fund = dict(fund)
        for field in ("fundStartDate", "fundEndDate"):
            if field in fund:
                fund[field] = _date(fund[field])
        _put(
            corpus,
            "fund",
            str(identifier),
            _fields(
                fund,
                (
                    "fundId",
                    "name",
                    "active",
                    "fundStartDate",
                    "fundEndDate",
                    "requiresPledges",
                ),
            ),
        )
    return corpus


def _ministries(corpus, source, as_of):
    """Retain catalog presence and dated roster evidence without fabricating status."""
    for identifier, row in _entries(source.ministry_types, "id"):
        _put(
            corpus,
            "ministry",
            str(identifier),
            {
                **_fields(row, ("id", "name", "active")),
                "catalog_present": True,
            },
        )
    for identifier, group in source.ministry_type_memberships.items():
        if str(_id(identifier)) not in corpus["ministry"]:
            raise InvalidSourcePayload("Source roster has no retained Ministry.")
        if type(group) is not dict or type(group.get("membership")) is not list:
            raise InvalidSourcePayload("Source roster membership is not a collection.")
        for row in group["membership"]:
            if type(row) is not dict:
                raise InvalidSourcePayload("Source roster entry is not a record.")
            member_id = _id(row.get("memberDUID", row.get("memberId")))
            if str(member_id) not in corpus["member"]:
                raise InvalidSourcePayload("Source roster has no retained Member.")
            values = _fields(
                row,
                (
                    "ministryRoleId",
                    "ministryRoleName",
                    "ministryEventId",
                    "ministryEventName",
                    "trained",
                    "subOnly",
                ),
            )
            start, end = _date(row.get("startDate")), _date(row.get("endDate"))
            identity = {
                "member": member_id,
                "ministry": identifier,
                "role": (
                    values["ministryRoleId"]
                    if values.get("ministryRoleId") is not None
                    else values.get("ministryRoleName")
                ),
                "event": values.get("ministryEventId"),
                "start": start,
            }
            encoded, _ = canonical_payload(identity)
            key = hashlib.sha256(encoded.encode()).hexdigest()
            _put(
                corpus,
                "roster",
                key,
                {
                    **values,
                    "member_key": str(member_id),
                    "ministry_key": str(identifier),
                    "startDate": start,
                    "endDate": end,
                    "current": ministry_membership_is_current(
                        {"startDate": start, "endDate": end}, today=as_of
                    ),
                },
            )
