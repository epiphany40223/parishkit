"""Private directory filters and bounded, coherent source/credential reads."""

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import connection
from django.db.models import Q
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.accounts.cryptography import canonical_code
from parishkit.stewardship.campaigns.credential_keys import key_set_lock
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    FamilyCodeFingerprint,
)
from parishkit.stewardship.campaigns.family_identity import code_context
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.observability import FailureKind
from parishkit.stewardship.source.family_names import (
    family_heads_name,
    name_series,
)
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.tables import Sorting

from .directory_query import DIRECTORY
from .information import parse_page

PAGE_SIZE = 50
REASONS = {
    "no_head": "No active Family head",
    "no_address": "No head email address",
    "invalid_address": "No valid head email address",
    "provider_refused": "All eligible addresses permanently refused",
    "deliverable": "Deliverable email available",
}
# How campaign mail can reach a Family: by deliverable email, only by postal
# mail (a usable mailing address, see directory_reports.sql), or neither.
REACH = {
    "email": "By email",
    "mail": "By postal mail only",
    "neither": "Neither email nor postal mail",
}

# The installed selection (stewardship_directory_report_v1) orders and pages
# the directory itself, 50 rows at a time, so its closed ``sort`` vocabulary
# is the whole list of column sorts: Family by the shown name (surname
# first) either way, and DUID ascending. The other columns cannot be sorted
# without changing that frozen SQL (schema freeze, #203): Family code would
# also mean decrypting every Family's code per page view, and email
# deliverability, response, addressee, mailing address and contact details
# are computed per row inside the selection.
DIRECTORY_SORTING = Sorting(
    {
        "name": ("family", False),
        "name_desc": ("family", True),
        "duid": ("duid", False),
    },
    "name",
)


@dataclass(frozen=True, repr=False)
class DirectoryQuery:
    """Identifying values travel only in CSRF POST bodies, never links or logs."""

    search: str = ""
    exact_code: str = ""
    reason: str = "any"
    phone: str = "any"
    response: str = "any"
    sort: str = "name"
    reach: str = "any"
    page: int = 1

    @classmethod
    def parse(cls, parameters):
        """Reject duplicate/unknown values and canonicalize friendly manual codes."""
        if type(parameters) is dict:
            if any(type(value) is not str for value in parameters.values()):
                raise ValueError("Directory filters require text values.")
            parameters = MultiValueDict(
                {key: [value] for key, value in parameters.items()}
            )
        values = filters(parameters, allowed=set(cls.__dataclass_fields__))
        values["page"] = parse_page(values.get("page", "1"))
        query = cls(**values)
        bounded_text(query.search)
        if (
            len(query.search) > 200
            or query.reason not in {"any", *REASONS}
            or query.phone not in {"any", "yes", "no"}
            or query.response not in {"any", "yes", "no"}
            or query.sort not in DIRECTORY_SORTING.tokens
            or query.reach not in {"any", *REACH}
        ):
            raise ValueError("Invalid directory filters.")
        if query.exact_code:
            code = canonical_code(query.exact_code)
            if code is None:
                raise ValueError("Invalid exact-code filter.")
            values["exact_code"] = code
        return cls(**values)

    def form_values(self):
        """Templates escape private POST state for filters and page navigation."""
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key != "page"
        }

    def audit_values(self):
        """Only closed filter dimensions/presence flags may enter durable audit."""
        return {
            "directory_reason": self.reason,
            "directory_phone": self.phone,
            "directory_response": self.response,
            "directory_sort": self.sort,
            "page": self.page,
            "search_used": bool(self.search),
            "exact_code_used": bool(self.exact_code),
        }


def testing_codes_context(campaign_id):
    """Template context for the Testing-mode note about live Family codes.

    In Testing mode the Family sign-in accepts only rehearsal credentials from
    a chosen-Family test send, so a code copied from the directory is refused.
    The note says so and links to that test send. The Family-facing denial
    stays reason-free; only Admin and Staff pages explain it.
    """
    from parishkit.stewardship.accounts.campaign_family_test import (
        chosen_family_test_url,
    )
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.models import Campaign

    runtime = SystemConfiguration.objects.first()
    if runtime is None or runtime.mode != "testing":
        return {"testing_codes": False, "family_test_url": None}
    campaign = Campaign.objects.filter(pk=campaign_id).first()
    return {
        "testing_codes": True,
        "family_test_url": chosen_family_test_url(runtime, campaign),
    }


def address_lines(address):
    """Render known nonblank components without displaying Python null values."""

    def value(key):
        """A missing and a known-blank component both contribute no printed text."""
        return str(address.get(key) or "").strip()

    lines = [value(f"primaryAddress{index}") for index in (1, 2, 3)]
    postal = value("primaryPostalCode")
    plus = value("primaryZipPlus")
    lines.append(
        " ".join(
            filter(
                None,
                (
                    value("primaryCity"),
                    value("primaryState"),
                    "-".join(filter(None, (postal, plus))),
                ),
            )
        )
    )
    return tuple(filter(None, lines))


def selection_parameters(campaign_id, query, *, postal, mac):
    """Bind private exact-code input to an immutable Family ID under key locks.

    Persisting this selection never persists a plaintext code or a MAC key
    version. Regeneration therefore remains stable after ordinary key rotation.
    Unknown codes retain an explicit empty exact selection, not an unfiltered
    query. Callers hold the campaign read/work barrier and key inventory lock.
    """
    if not isinstance(campaign_id, UUID) or not isinstance(query, DirectoryQuery):
        raise ValueError("Directory selection requires typed scope and filters.")
    if type(postal) is not bool:
        raise ValueError("Directory kind must be explicit.")
    query = DirectoryQuery.parse(query.form_values() | {"page": str(query.page)})
    family_id = None
    if query.exact_code:
        candidates = mac.lookups(campaign_id, query.exact_code)
        choices = Q(pk__in=[])
        for key, digest in candidates.items():
            choices |= Q(key_id=key, digest=digest)
        identities = list(
            FamilyCodeFingerprint.objects.filter(choices, campaign_id=campaign_id)
            .values_list("family_id", flat=True)
            .distinct()[:2]
        )
        if len(identities) > 1:
            raise ReadUnavailable("Exact-code selection is unavailable.")
        family_id = str(identities[0]) if identities else None
    values = query.form_values()
    values.pop("exact_code")
    return {
        "filters": values,
        "postal": postal,
        "exact": bool(query.exact_code),
        "family_id": family_id,
    }


def add_codes(campaign_id, rows, *, general):
    """Decrypt only the selected identities under the caller's key/read barriers.

    Also adds each row's presentation values: the reason label, the address
    lines and ``display_name``, the surname followed by the heads of household
    (the same string the SQL selection searched and ordered by).
    """
    identities = {
        str(row.pk): row
        for row in FamilyCampaign.objects.filter(
            campaign_id=campaign_id,
            pk__in=[row["family_id"] for row in rows if row["family_id"]],
        ).only("id", "code_ciphertext")
    }
    for row in rows:
        identity = identities.get(row["family_id"])
        row["code"] = (
            general.decrypt(
                identity.code_ciphertext, context=code_context(identity.pk)
            ).decode("ascii")
            if identity and identity.code_ciphertext
            else None
        )
        row["reason_label"] = REASONS[row["reason"]]
        row["address_lines"] = address_lines(row["address"])
        row["display_name"] = family_heads_name(row["family_name"], row["heads"])


# One statement chooses the snapshot to read head contacts from and reads
# them. It is the captured snapshot while that is still promoted and not
# compacted; otherwise, only when ``current`` is true, the current promoted
# snapshot (an export rendered, retried or regenerated after the 15-minute
# refresh compacted its capture's source). Compaction marks a snapshot
# compacted before deleting any of its memberships, so within this one
# statement's view the chosen snapshot's memberships are all there; missing
# contacts are never mistaken for "no email". No row at all means no
# snapshot qualified. The LEFT JOINs keep the chosen snapshot's row when
# none of the heads has a contact record.
HEAD_CONTACTS = """
WITH captured AS (
    SELECT s.id FROM stewardship_source_snapshot s
    WHERE s.id=%(captured)s AND s.state='promoted' AND s.compacted_at IS NULL
), chosen AS (
    SELECT s.id,s.promoted_at FROM stewardship_source_snapshot s
    WHERE s.state='promoted' AND s.compacted_at IS NULL
      AND s.id=coalesce((SELECT id FROM captured),
          (SELECT c.snapshot_id FROM stewardship_source_current c
           WHERE c.singleton AND %(current)s))
)
SELECT x.id,x.promoted_at,
    -- Which of the heads the chosen snapshot still has as Members at all.
    ARRAY(SELECT m.source_key FROM stewardship_snapshot_member m
          WHERE m.snapshot_id=x.id AND m.source_key=ANY(%(members)s)),
    c.source_key,p.canonical
FROM chosen x
LEFT JOIN stewardship_snapshot_contact c
    ON c.snapshot_id=x.id AND c.source_key=ANY(%(keys)s)
LEFT JOIN stewardship_source_contact p ON p.id=c.payload_id
"""
INVALID_EMAIL_NOTE = "not a valid address; fix in ParishSoft"
# A captured head the current ParishSoft data (an export's fallback) no
# longer has as a Member, so "no email" would be a guess.
MISSING_HEAD_NOTE = "(not in current ParishSoft data)"


class HeadEmailsUnavailable(ReadUnavailable):
    """No ParishSoft data is available to read Family head emails from.

    The page raises it when its own source was compacted mid-request (the
    next request reads the new source); an export only when neither its
    captured source nor any current promoted source exists. Its failure kind
    names it in the logs; the exception text never reaches a log line.
    """

    failure_kind = FailureKind.HEAD_EMAILS_UNAVAILABLE


def _head_contacts(source_id, keys, *, current=False):
    """Contact JSON by source key for ``keys`` and the snapshot it came from.

    Returns ``(contacts, (snapshot_id, promoted_at, members))``, where
    ``members`` is the set of the heads' DUIDs that snapshot has as Members,
    or ``({}, None)`` when there are no keys (nothing is read). With
    ``current`` a compacted ``source_id`` falls back to the current promoted
    snapshot; otherwise, and when no snapshot qualifies,
    ``HeadEmailsUnavailable`` is raised rather than reporting every head as
    having no email.
    """
    if not keys:
        return {}, None
    with connection.cursor() as cursor:
        cursor.execute(
            HEAD_CONTACTS,
            {
                "captured": str(source_id),
                "current": current,
                "keys": sorted(keys),
                "members": sorted(key.removeprefix("member:") for key in keys),
            },
        )
        found = cursor.fetchall()
    if not found:
        raise HeadEmailsUnavailable(
            "Family head emails are unavailable: no current ParishSoft data."
            if current
            else "Family head emails are unavailable: the ParishSoft data was "
            "just replaced; try again."
        )
    contacts = {key: json.loads(canonical) for *_, key, canonical in found if key}
    snapshot, promoted_at, members = found[0][:3]
    return contacts, (snapshot, promoted_at, set(members))


def head_email_groups(heads):
    """Each distinct head email once, with every head who has it (#604).

    Addresses are compared case-insensitively after trimming, so heads who
    share one ("Anna Example and Ben Example") are listed together; a head
    with an address of their own gets a separate entry, and a head with no
    email an entry whose ``value`` is ``None`` (``missing`` when an export's
    current data no longer has that head at all). Entries follow the heads'
    order (DUID order from the selection), then each head's address order,
    so the result is deterministic. Each entry is ``{"names", "value",
    "valid", "missing"}``; ``valid`` is false for source text that is not an
    address.
    """
    groups = {}
    for index, head in enumerate(heads):
        emails = head.get("emails") or ()
        if not emails:
            # A tuple key can never collide with an address key.
            groups[(index,)] = {
                "heads": [head["name"]],
                "value": None,
                "valid": False,
                "missing": bool(head.get("missing")),
            }
        for email in emails:
            value = email["value"].strip()
            group = groups.setdefault(
                value.casefold(),
                {"heads": [], "value": value, "valid": email["valid"], "seen": set()},
            )
            # One head holding two case variants of an address is named once.
            if index not in group["seen"]:
                group["seen"].add(index)
                group["heads"].append(head["name"])
    return [
        {
            "names": name_series(group["heads"]),
            "value": group["value"],
            "valid": group["valid"],
            "missing": group.get("missing", False),
        }
        for group in groups.values()
    ]


def head_emails_text(heads):
    """One export cell: "Anna and Ben Example: a@x; Cara Example: (no email)"."""
    parts = []
    for group in head_email_groups(heads):
        if group["missing"]:
            value = MISSING_HEAD_NOTE
        elif group["value"] is None:
            value = "(no email)"
        elif group["valid"]:
            value = group["value"]
        else:
            value = f"{group['value']} ({INVALID_EMAIL_NOTE})"
        parts.append(f"{group['names']}: {value}")
    return "; ".join(parts)


def add_head_emails(source_id, rows, *, current=False):
    """Give each row's heads their emails from the rows' own source snapshot.

    The heads are exactly those the SQL selection returned for the Family
    name, so the two never disagree. One read covers all ``rows`` (none when
    no row has a head). The emails are not part of the installed selection,
    which would need a schema change (#604). Each head gains ``emails``, a
    list of ``{"value": text, "valid": bool}`` (invalid source text is kept so
    staff can correct it in ParishSoft), and each row gains ``head_emails``,
    the de-duplicated ``head_email_groups`` the page shows.

    ``current`` lets an export read the same heads' emails from the current
    ParishSoft data once its captured source is compacted. Returns that
    data's promotion time when it was used, so the file can say how current
    its emails are, and ``None`` when the captured source was read (or
    nothing was). With the current data, a head it no longer has as a Member
    is marked ``missing`` ("(not in current ParishSoft data)", not "no
    email").
    """
    contacts, chosen = _head_contacts(
        source_id,
        {f"member:{head['duid']}" for row in rows for head in row["heads"]},
        current=current,
    )
    fallback = chosen is not None and str(chosen[0]) != str(source_id)
    for row in rows:
        for head in row["heads"]:
            contact = contacts.get(f"member:{head['duid']}")
            head["emails"] = contact["emails"] if contact else []
            head["missing"] = fallback and str(head["duid"]) not in chosen[2]
        row["head_emails"] = head_email_groups(row["heads"])
    return chosen[1] if fallback else None


def directory_page(campaign_id, query, *, postal, general, mac):
    """Read one SQL page, then decrypt only its codes under the caller's read guard.

    Campaign identity/code values never change during retained campaign life.
    The key-inventory lock prevents rotation between selection and decryption;
    the outer campaign read guard prevents purge through response completion.
    No Family session, public limiter or opaque email token is involved.
    """
    with key_set_lock(general, mac):
        parameters = selection_parameters(campaign_id, query, postal=postal, mac=mac)
        with connection.cursor() as cursor:
            cursor.execute(
                DIRECTORY,
                (campaign_id, json.dumps(parameters), query.page),
            )
            result = cursor.fetchone()
        if result is None or result[0] is None:
            raise ReadUnavailable("Directory source information is unavailable.")
        report = json.loads(result[0])
        add_codes(campaign_id, report["rows"], general=general)
        add_head_emails(report["metadata"]["source_id"], report["rows"])
        report["metadata"]["source_as_of"] = datetime.fromisoformat(
            report["metadata"]["source_as_of"]
        )
        return report


# How many matches the header's Find a Family box lists (#561); more than
# this offers the whole list in the active parishioner family directory instead.
FIND_LIMIT = 8
# The shortest search the box sends. One character would match nearly every
# Family (any name with that letter, any DUID with that digit), so a lookup
# that short is never useful and only costs a selection.
FIND_MINIMUM = 2


def find_families(campaign_id, query):
    """The first ``FIND_LIMIT`` Families the directory search finds (#561).

    Runs the installed directory selection itself, so the header's Find a
    Family box matches exactly what the directory page's search matches
    (the shown name with its heads, DUID and address) and lists in the same
    order. Only the search filter is set. Unlike ``directory_page`` it
    decrypts no Family code and reads no head emails: a match shows only
    its name, DUID and envelope number. The caller holds the campaign read
    guard, as for the directory page. Returns ``{"rows", "total"}``; each row
    has ``family_id`` (None for a Family without a campaign record),
    ``display_name``, ``family_duid`` and ``envelope``.
    """
    if query != DirectoryQuery(search=query.search) or (
        len(query.search.strip()) < FIND_MINIMUM
    ):
        raise ValueError("Find a Family takes only a search of 2 or more characters.")
    # selection_parameters needs the MAC ring only for an exact-code filter,
    # which this query never has.
    parameters = selection_parameters(campaign_id, query, postal=False, mac=None)
    with connection.cursor() as cursor:
        cursor.execute(DIRECTORY, (campaign_id, json.dumps(parameters), 1))
        result = cursor.fetchone()
    if result is None or result[0] is None:
        raise ReadUnavailable("Directory source information is unavailable.")
    report = json.loads(result[0])
    return {
        "rows": [
            {
                "family_id": row["family_id"],
                "display_name": family_heads_name(row["family_name"], row["heads"]),
                "family_duid": row["family_duid"],
                "envelope": row["envelope"],
            }
            for row in report["rows"][:FIND_LIMIT]
        ],
        "total": report["total"],
    }
