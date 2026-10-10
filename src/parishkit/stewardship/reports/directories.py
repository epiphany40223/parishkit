"""Private directory filters and bounded, coherent source/credential reads."""

import json
from dataclasses import dataclass, replace
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

# The Response filter (#933): which Families the response funnel selects, by
# the same rules the response lists used, so a list's length equals its
# Response dashboard tile. "any" (the first choice) keeps the active Families.
# "not-submitted" (the earlier "Not yet responded") is split with no overlap
# by "started", "never-opened" and "not-invited" for the Families with a
# campaign record; "not-submitted" alone also lists active Families without
# one. "not-invited" is a response status (no invitation email delivered),
# not a postal list (#951; postal lists are the mailing columns).
RESPONSES = {
    "submitted": "Submitted",
    "more-than-once": "Submitted more than once",
    "not-submitted": "Not submitted",
    "started": "Started, not submitted",
    "progressed": "Started, got past the first step",
    "opened-only": "Opened the form only",
    "never-opened": "Invited, never opened",
    "link-followed": "Invited, link followed but never opened",
    "link-not-followed": "Invited, link not followed",
    "not-invited": "No invitation delivered",
}
# The choices that list active Families only; every other choice also lists
# the campaign's Families no longer active in ParishSoft (see
# stewardship_directory_report_v2), so the page header drops "active".
ACTIVE_ONLY_RESPONSES = frozenset({"any", "not-submitted", "not-invited"})
# The ParishSoft data to check filter (#933): the two launch-day problems.
CHECKS = {
    "anything": "Anything to check",
    "mailing-name": "Blank mailing name",
    "envelope": "Envelope number 0",
}
# What to check, by the selection's row flag, in the page's words.
CHECK_FLAGS = (
    ("mailing_name_blank", "Blank mailing name"),
    ("envelope_zero", "Envelope number 0"),
)
# A row the Response filter lists although the current ParishSoft data no
# longer lists the Family as active and registered (reason "inactive"), and
# the name shown when that data no longer has the Family at all.
INACTIVE = "No longer active in ParishSoft"
MISSING_NAME = "Not in the latest ParishSoft data"


@dataclass(frozen=True)
class ResponseColumn:
    """One response column: its sort key, heading, row field and missing words.

    ``count`` marks Submissions, a number rather than an instant.
    """

    key: str
    heading: str
    field: str
    missing: str = "Not yet"
    count: bool = False


RESPONSE_COLUMNS = {
    column.key: column
    for column in (
        ResponseColumn("invited", "Invitation delivered", "invited_at"),
        ResponseColumn("link", "Link followed", "link_at", "No"),
        ResponseColumn("opened", "Form opened", "form_opened_at"),
        ResponseColumn("progressed", "Got past the first step", "progressed_at"),
        ResponseColumn("submitted", "First submitted", "submitted_at"),
        ResponseColumn("last", "Last submitted", "last_submitted_at"),
        ResponseColumn("submissions", "Submissions", "submissions", "", count=True),
    )
}
# The response columns each Response choice shows, as the response lists
# showed them: the dates in the order the events happen (they lead the row,
# #932), then Submissions where a list had it (after the envelope number).
RESPONSE_COLUMN_SETS = {
    "any": ("invited", "opened", "submitted", "submissions"),
    "submitted": ("submitted", "submissions"),
    "more-than-once": ("submitted", "last", "submissions"),
    "started": ("opened", "progressed"),
    "progressed": ("opened", "progressed"),
    "opened-only": ("opened", "progressed"),
    "never-opened": ("invited", "link"),
    "link-followed": ("invited", "link"),
    "link-not-followed": ("invited", "link"),
    "not-submitted": ("invited", "opened", "progressed"),
    "not-invited": ("link", "opened"),
}


def response_columns(response):
    """The (date columns, count columns) the Response choice shows."""
    columns = [RESPONSE_COLUMNS[key] for key in RESPONSE_COLUMN_SETS[response]]
    return (
        tuple(column for column in columns if not column.count),
        tuple(column for column in columns if column.count),
    )


# The installed selection (stewardship_directory_report_v2) orders and pages
# the directory itself, 50 rows at a time, so its closed ``sort`` vocabulary
# is the whole list of column sorts: Family by the shown name (surname
# first) either way, DUID ascending, and each response column either way
# (#933; times and counts newest or largest first on the first click, as the
# response lists sorted them). The other columns cannot be sorted without
# changing that frozen SQL: Family code would also mean decrypting every
# Family's code per page view, and email deliverability, response, addressee,
# mailing address and contact details are computed per row inside the
# selection.
DIRECTORY_SORTING = Sorting(
    {
        "name": ("family", False),
        "name_desc": ("family", True),
        "duid": ("duid", False),
        **{
            token: (key, descending)
            for key in RESPONSE_COLUMNS
            for token, descending in ((f"{key}_desc", True), (key, False))
        },
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
    # ParishSoft data to check (#933) and whether the response columns show:
    # closed values, like the filters above.
    check: str = "any"
    responses: str = "no"
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
            or query.response not in {"any", *RESPONSES}
            or query.sort not in DIRECTORY_SORTING.tokens
            or query.reach not in {"any", *REACH}
            or query.check not in {"any", *CHECKS}
            or query.responses not in {"yes", "no"}
        ):
            raise ValueError("Invalid directory filters.")
        if query.exact_code:
            code = canonical_code(query.exact_code)
            if code is None:
                raise ValueError("Invalid exact-code filter.")
            values["exact_code"] = code
        if not query.sorted_column_shown():
            values["sort"] = "name"
        return cls(**values)

    def postal(self):
        """These filters as the mail merge applies them: "By postal mail only".

        Postal invitations go only to active parishioner Families with no
        deliverable head email (Administrator decision, #951), the rule the
        invitation sender uses (``source.families.family_recipients``). Reach
        ``mail`` keeps the active Families without deliverable head email
        that have a usable mailing address: a Family no longer active in
        ParishSoft, which some Response choices list (#933), has no postal
        reach, so it is left out; Families with neither are on the
        "neither" list. So whichever reach was asked for, mailing columns
        use ``mail``.
        """
        return replace(self, reach="mail")

    def sorted_column_shown(self):
        """Whether the column the sort orders by is on the page and export.

        A response column shows only with Include response columns ticked and
        only for the Response choices whose list had it
        (``RESPONSE_COLUMN_SETS``). ``parse`` falls back to the name order
        otherwise, so the page, the export and the audit entry never order
        by a column nobody can see (#933).
        """
        column = self.sort.removesuffix("_desc")
        return column not in RESPONSE_COLUMNS or (
            self.responses == "yes" and column in RESPONSE_COLUMN_SETS[self.response]
        )

    def active_only(self):
        """Whether the Response choice lists active Families only (#933)."""
        return self.response in ACTIVE_ONLY_RESPONSES

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
            # How mail can reach the listed Families (#388 L1).
            "directory_reach": self.reach,
            # ParishSoft data to check and the response columns (#933).
            "directory_data_check": self.check,
            "directory_response_columns": self.responses == "yes",
            "page": self.page,
            "search_used": bool(self.search),
            "exact_code_used": bool(self.exact_code),
        }


def testing_codes_context(campaign_id, principal):
    """Template context for the Testing-mode note about live Family codes.

    In Testing mode the Family sign-in accepts only rehearsal credentials from
    a chosen-Family test send, so a code copied from the directory is refused.
    The note says so and, for an Administrator, links to that test send. The
    test-send page is Administrator only (CONFIGURE), so Staff get no link
    (#591) and the note asks them to have an Administrator send one instead.
    The Family-facing denial stays reason-free; only Admin and Staff pages
    explain it.
    """
    from parishkit.stewardship.accounts.campaign_family_test import (
        chosen_family_test_url,
    )
    from parishkit.stewardship.accounts.policy import Capability, allows
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.campaigns.models import Campaign

    runtime = SystemConfiguration.objects.first()
    if runtime is None or runtime.mode != "testing":
        return {"testing_codes": False, "family_test_url": None}
    administrator = allows(principal, Capability.CONFIGURE)
    url = None
    if administrator:
        campaign = Campaign.objects.filter(pk=campaign_id).first()
        url = chosen_family_test_url(runtime, campaign)
    return {
        "testing_codes": True,
        "testing_codes_administrator": administrator,
        "family_test_url": url,
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
    query. A postal (mail-merge) selection always has reach ``mail``; see
    ``DirectoryQuery.postal``. Callers hold the campaign read/work barrier and
    key inventory lock.
    """
    if not isinstance(campaign_id, UUID) or not isinstance(query, DirectoryQuery):
        raise ValueError("Directory selection requires typed scope and filters.")
    if type(postal) is not bool:
        raise ValueError("Directory kind must be explicit.")
    query = DirectoryQuery.parse(query.form_values() | {"page": str(query.page)})
    if postal:
        # Every new mail merge, page view and CLI request lists only the
        # Families postal invitations are for (#951).
        query = query.postal()
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
    # The response columns choose columns, not rows, like ``postal``.
    responses = values.pop("responses") == "yes"
    return {
        "filters": values,
        "responses": responses,
        "postal": postal,
        "exact": bool(query.exact_code),
        "family_id": family_id,
    }


def add_codes(campaign_id, rows, *, general):
    """Decrypt only the selected identities under the caller's key/read barriers.

    Also adds each row's presentation values: the reason label, the address
    lines and ``display_name``, the surname followed by the heads of household
    (the same string the SQL selection searched and ordered by), plus the
    response values (``add_responses``).
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
        row["reason_label"] = (
            INACTIVE if row["reason"] == "inactive" else REASONS[row["reason"]]
        )
        row["address_lines"] = address_lines(row["address"])
        row["display_name"] = row_name(row)
    add_responses(rows)


def row_name(row):
    """The Family as shown: surname and heads, or why there is no name.

    A Family the Response filter lists after the current ParishSoft data
    dropped it entirely has no name (``family_name`` None, #933).
    """
    if row["family_name"] is None:
        return MISSING_NAME
    return family_heads_name(row["family_name"], row["heads"])


def add_responses(rows):
    """Give each row its response instants as datetimes and its data checks.

    The selection returns the funnel instants as ISO text (null when it did
    not read the funnel or the Family had not reached the stage); captures
    made before #933 have none of these keys, and read as nothing reached.
    ``checks`` lists what to check in the ParishSoft record, in words.
    """
    for row in rows:
        for column in RESPONSE_COLUMNS.values():
            value = row.get(column.field)
            if column.count:
                row[column.field] = value or 0
            elif isinstance(value, str):
                row[column.field] = datetime.fromisoformat(value)
            else:
                row[column.field] = None
        row["active"] = row.get("active", True)
        row["checks"] = [label for flag, label in CHECK_FLAGS if row.get(flag)]


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
        metadata = report["metadata"]
        metadata["source_as_of"] = datetime.fromisoformat(metadata["source_as_of"])
        # When the response funnel was read: the page's "Counted at" (#933).
        if metadata.get("counted_at"):
            metadata["counted_at"] = datetime.fromisoformat(metadata["counted_at"])
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
    (the shown name with its heads, any active Member's name, DUID, envelope
    number and address; #664). Only the search filter is set. The box lists
    matches in the directory's order, except that a Family whose envelope
    number or DUID is exactly the search text comes first (#712; see
    ``_exact_rows``). Unlike ``directory_page`` it decrypts no Family code
    and reads no head emails: a match shows only its name, DUID and envelope
    number. The caller holds the campaign read guard, as for the directory
    page. Returns ``{"rows", "total"}``; each row
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
    report = _selection(campaign_id, parameters)
    rows = _exact_rows(campaign_id, parameters, report, query.search.strip())
    first = {row["family_duid"] for row in rows}
    rows += [row for row in report["rows"] if row["family_duid"] not in first]
    return {
        "rows": [
            {
                "family_id": row["family_id"],
                "display_name": row_name(row),
                "family_duid": row["family_duid"],
                "envelope": row["envelope"],
            }
            for row in rows[:FIND_LIMIT]
        ],
        "total": report["total"],
    }


def _selection(campaign_id, parameters):
    """The installed directory selection's first page for ``parameters``."""
    with connection.cursor() as cursor:
        cursor.execute(DIRECTORY, (campaign_id, json.dumps(parameters), 1))
        result = cursor.fetchone()
    if result is None or result[0] is None:
        raise ReadUnavailable("Directory source information is unavailable.")
    return json.loads(result[0])


# At most this many exact-number Families are looked up: in practice one
# DUID and one envelope number can equal the same search text.
EXACT_LIMIT = 2
# The campaign records of the Families in the selection's own source whose
# DUID or envelope number is exactly the search text. The envelope number is
# only in the payload JSON, so this reads the source's Family rows once (one
# snapshot's rows, found by its index; no per-Family work). Only Families the
# directory lists (the selection's portal_eligible rule) count, so ineligible
# ones cannot use up the limit and keep an eligible Family from moving up.
EXACT_FAMILIES = """
SELECT fc.id FROM stewardship_snapshot_family m
JOIN stewardship_source_family p ON p.id=m.payload_id
JOIN stewardship_family_campaign fc
    ON fc.campaign_id=%(campaign)s AND fc.family_duid=m.source_key::bigint
WHERE m.snapshot_id=%(source)s
  AND p.canonical::jsonb->'portal_eligible'='true'::jsonb
  AND (m.source_key=%(number)s OR p.canonical::jsonb->>'envelopeNumber'=%(number)s)
ORDER BY m.source_key::bigint LIMIT %(limit)s
"""


def _exact_rows(campaign_id, parameters, report, text):
    """The selection rows whose envelope number or DUID is exactly ``text``.

    Staff typing a number from a gift usually want that one Family, but a
    short number such as "12" also matches every DUID, address and name
    containing it, so the Family may be past the first 8 matches or past
    the selection's first page of 50 (#712). Reading every match instead
    took close to a minute per search at 2,700 Families (address and postal
    code matches make most digit searches broad), so this reads only page 1
    and, when that page does not hold every match, looks the exact Families
    up directly. Each one found is then read through the same selection
    with the same search, restricted to that Family, so it is listed only
    when the directory search would list it too: the set of matches, the
    total and the audit counts never change. That costs one indexed lookup
    and at most ``EXACT_LIMIT`` one-Family selections, only for an all-digit
    search with more matches than one page. The ranking runs here rather
    than in the installed selection so that it needs no schema change.

    One limit follows from reading through the selection: a Family without
    a campaign record (``family_id`` None) cannot be read alone, so it keeps
    its place instead of moving up.
    """
    if not _whole_number(text):
        return []
    exact = [
        row
        for row in report["rows"]
        if text in (str(row["envelope"]), str(row["family_duid"]))
    ]
    if report["total"] <= len(report["rows"]):
        return exact
    with connection.cursor() as cursor:
        cursor.execute(
            EXACT_FAMILIES,
            {
                "campaign": campaign_id,
                "source": report["metadata"]["source_id"],
                "number": text,
                "limit": EXACT_LIMIT,
            },
        )
        identities = [str(identity) for (identity,) in cursor.fetchall()]
    shown = {row["family_id"] for row in exact}
    for identity in identities:
        if identity not in shown:
            one = parameters | {"exact": True, "family_id": identity}
            exact += _selection(campaign_id, one)["rows"]
    return exact


def _whole_number(text):
    """Whether ``text`` is ASCII digits only, like envelope numbers and DUIDs."""
    return text.isascii() and text.isdigit()
