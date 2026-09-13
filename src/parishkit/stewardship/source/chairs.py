"""Source-derived Chairperson suggestions, never inferred authorization grants.

Inputs are the normalized, pinned source corpus and verified applied policy.
The owning database service must bind both to its transaction. These pure
records contain private contact information: only Admin-authorized views may
render them. Merely computing a suggestion cannot create a login or assignment.
"""

from dataclasses import dataclass, field

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.ministry_activity import active_ministries
from parishkit.stewardship.accounts.policy_schema import normalized_email

from .canonical import InvalidSourcePayload


@dataclass(frozen=True)
class ChairCandidate:
    """One explicit Member/Ministry/email relationship with exact roster evidence."""

    organization_id: int
    member_duid: int
    ministry_duid: int
    email: str = field(repr=False)
    member_name: str = field(repr=False)
    ministry_name: str = field(repr=False)
    publish_email: bool
    roster_keys: tuple[str, ...]


@dataclass(frozen=True)
class ChairSuggestion:
    """Grouped suggestions retain every candidate instead of picking a shared email."""

    email: str = field(repr=False)
    ministry_duid: int
    candidates: tuple[ChairCandidate, ...]
    email_member_duids: tuple[int, ...]

    @property
    def ambiguous(self):
        """Also flag another active non-chair Member using this same login address."""
        return len(self.email_member_duids) != 1


def _identity(key):
    """Require an exact canonical source key without coercing malformed identities."""
    if type(key) is not str or not key.isascii() or not key.isdecimal():
        raise ValueError("Invalid source key.")
    number = int(key)
    if str(number) != key or not 1 <= number < 2**31:
        raise ValueError("Invalid source key.")
    return number


def _schema(payload):
    """Do not treat unknown or boolean schema versions as equivalent to v1."""
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("Invalid source schema.")


def _name(payload, fields):
    """Preserve available display names without inventing or requiring missing parts."""
    parts = []
    for name in fields:
        value = payload.get(name)
        if value is not None:
            if type(value) is not str:
                raise ValueError("Invalid display name.")
            if value:
                parts.append(value)
    return " ".join(parts)


def _members(corpus):
    """Index active Member addresses independently of roles and publication flags."""
    members, owners = {}, {}
    for key, member in corpus["member"].items():
        identifier = _identity(key)
        _schema(member)
        if (
            type(member["memberDUID"]) is not int
            or member["memberDUID"] != identifier
            or type(member["active"]) is not bool
        ):
            raise ValueError("Invalid Member activity.")
        if not member["active"]:
            continue
        contact = corpus["contact"].get(f"member:{key}")
        if contact is None:
            continue
        _schema(contact)
        if (
            contact["owner_kind"] != "member"
            or contact["owner_key"] != key
            or type(contact["publish_email"]) is not bool
            or type(contact["emails"]) is not list
        ):
            raise ValueError("Invalid Member contact binding.")
        emails = set()
        for row in contact["emails"]:
            if type(row["valid"]) is not bool:
                raise ValueError("Invalid email eligibility.")
            if row["valid"]:
                email = normalized_email(row["value"])
                if email != row["value"]:
                    raise ValueError("Noncanonical Member email.")
                emails.add(email)
                owners.setdefault(email, set()).add(identifier)
        members[key] = (member, contact, tuple(sorted(emails)))
    return members, owners


def chair_suggestions(corpus, document, *, organization_id):
    """Group current active Chairpersons using local activity, not upstream flags.

    Multiple dated/event roster rows for the same relationship retain their
    evidence but yield one candidate per email. An invalid address contributes
    no candidate. Private-but-valid email remains visible to the Admin with its
    publication indicator. No email is guessed from the Family or another Member.
    """
    try:
        return _suggestions(corpus, document, organization_id)
    except (KeyError, TypeError, ValueError, ConfigError):
        raise InvalidSourcePayload("Source Chairperson data is inconsistent.") from None


def _suggestions(corpus, document, organization_id):
    """Validate source references, then build deterministic grouped candidates."""
    catalog = {}
    for key, ministry in corpus["ministry"].items():
        identifier = _identity(key)
        _schema(ministry)
        if (
            type(ministry["id"]) is not int
            or ministry["id"] != identifier
            or ministry["catalog_present"] is not True
        ):
            raise ValueError("Invalid source catalog presence.")
        catalog[identifier] = _name(ministry, ("name",))
    active = active_ministries(
        document, organization_id=organization_id, catalog_duids=frozenset(catalog)
    )
    members, owners = _members(corpus)
    relationships = {}
    for key, roster in corpus["roster"].items():
        _schema(roster)
        member, ministry = roster["member_key"], roster["ministry_key"]
        member_id, ministry_id = _identity(member), _identity(ministry)
        if (
            member not in corpus["member"]
            or ministry_id not in catalog
            or type(roster["current"]) is not bool
            or type(key) is not str
            or len(key) != 64
            or any(char not in "0123456789abcdef" for char in key)
        ):
            raise ValueError("Invalid source roster relationship.")
        role = roster.get("ministryRoleName")
        if role is not None and type(role) is not str:
            raise ValueError("Invalid roster role.")
        if (
            roster["current"]
            and role is not None
            # ParishSoft's role label is ASCII; match the SQL projection without
            # locale-dependent lowercasing or Unicode lookalike equivalences.
            and role.isascii()
            and role.lower() == "chairperson"
            and member in members
            and ministry_id in active
        ):
            relationships.setdefault((member_id, ministry_id), []).append(key)
    groups = {}
    for (member_id, ministry_id), evidence in relationships.items():
        member, contact, emails = members[str(member_id)]
        for email in emails:
            candidate = ChairCandidate(
                organization_id,
                member_id,
                ministry_id,
                email,
                _name(member, ("firstName", "middleName", "lastName")),
                catalog[ministry_id],
                contact["publish_email"],
                tuple(sorted(evidence)),
            )
            groups.setdefault((email, ministry_id), []).append(candidate)
    return tuple(
        ChairSuggestion(
            email,
            ministry,
            tuple(
                sorted(
                    candidates,
                    key=lambda row: (row.member_name.casefold(), row.member_duid),
                )
            ),
            tuple(sorted(owners[email])),
        )
        for (email, ministry), candidates in sorted(
            groups.items(),
            key=lambda item: (catalog[item[0][1]].casefold(), item[0][1], item[0][0]),
        )
    )
