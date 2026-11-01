"""Closed Ministry filters and a privacy-safe report projection."""

import json
from dataclasses import dataclass, replace
from datetime import date, datetime

from django.db import connection
from django.utils.datastructures import MultiValueDict
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.role_refusal import signed_out
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.responses.member_census import gender_word
from parishkit.stewardship.schema_primitives import timezone_names
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.dates import UnknownZone
from parishkit.stewardship.web.refusals import UserFacingDenied
from parishkit.stewardship.web.tables import PAGE_SIZES, Sorting

from .directories import address_lines
from .information import parse_page

PAGE_SIZE = 50
# The installed selection (stewardship_ministry_report_v3, migration 0064,
# through its actor-scoped stewardship_ministry_report_v4) orders and pages the
# rows, so a heading can only choose one of its sort values. The summary sorts
# by Ministry name or Ministry DUID (#960); the page shows no Ministry DUID
# and so offers only the name heading (#1123), while the command line keeps
# the DUID order. Joining, Leaving, Unresolved and
# Follow-up progress are not sortable because the selection has no order for
# them. Detail rows sort by Member name, Member DUID (a Member added on the
# form, with no DUID yet, last either way) or submission time; the status and
# contact columns have no selection order. The same ``duid`` tokens mean the
# Ministry DUID in the summary and the Member DUID in one Ministry's view.
SUMMARY_TOKENS = {"name", "name_desc", "duid", "duid_desc"}
SUMMARY_SORTING = Sorting(
    {
        "name": ("ministry", False),
        "name_desc": ("ministry", True),
        "duid": ("ministry_duid", False),
        "duid_desc": ("ministry_duid", True),
    },
    "name",
)
DETAIL_SORTING = Sorting(
    {
        "name": ("member", False),
        "name_desc": ("member", True),
        "duid": ("member_duid", False),
        "duid_desc": ("member_duid", True),
        "newest": ("submitted", True),
        "oldest": ("submitted", False),
    },
    "name",
)
STATES = {
    "any": "All states",
    "unresolved": "Unresolved",
    "new": "New",
    "in_progress": "In progress",
    "resolved": "Resolved",
    "closed_no_response": "Closed without response",
    "cancelled": "Cancelled",
    "superseded": "Superseded",
}
# Row labels. Follow-up has no assignee (#552); a request still stored as
# `assigned` from before then reads as New, because that state only ever
# meant "has an assignee". Screens, exports and packets share these labels.
# The frozen report selection (stewardship_ministry_report_v3) still returns each
# row's assignee email; nothing renders it any more.
STATE_LABELS = STATES | {"assigned": STATES["new"]}
# Dates arrived without the browser's zone (a tab opened before #558, or a
# zone the server's catalog lacks); they are never read in another zone.
ZONE_MESSAGE = (
    "The dates came without your computer's time zone. Go back, reload the "
    "page and apply the filters again; if this repeats, check your computer's "
    "time zone setting.\n"
)
# A Ministry an Administrator removed from a live campaign whose requests are
# kept (#342); SQL marks it in_campaign false.
NOT_IN_CAMPAIGN = "Removed from this campaign"
# Outcome labels by request kind (#1132), shared by every screen, export and
# packet, so they always agree. The stored code never changes: a leave
# request's `declined` means the Member did not leave, so it reads "Stayed in
# the Ministry", while a join request's reads as a decline. Read them only
# through outcome_label() and outcome_choices(), never by indexing these.
_JOIN_LABELS = {
    "joined": "Joined the Ministry",
    "leave_confirmed": "Left the Ministry",
    "declined": "Declined / no longer interested",
    "no_response": "No response",
    "duplicate": "Duplicate request",
    "other": "Other",
}
_LABELS = {
    "join": _JOIN_LABELS,
    "leave": _JOIN_LABELS | {"declined": "Stayed in the Ministry"},
}
# A list that mixes both kinds (the queue's Outcome filter with Request
# "Any") names both meanings of `declined`.
_ANY_LABELS = _JOIN_LABELS | {"declined": "Declined / stayed"}


def outcome_label(outcome, action, default=""):
    """One stored outcome's label for a join or leave request.

    ``action`` is the request's kind (``join`` or ``leave``); any other value
    (such as ``any``) gives the combined label. A missing or unknown outcome
    gives ``default``.
    """
    return _LABELS.get(action, _ANY_LABELS).get(outcome, default)


def outcome_choices(action):
    """Every outcome code with its label for ``action``, in display order."""
    return {key: outcome_label(key, action) for key in _JOIN_LABELS}


@dataclass(frozen=True, repr=False)
class MinistryQuery:
    """Private search/date state travels only in bounded CSRF POST forms."""

    search: str = ""
    history: str = "current"
    state: str = "any"
    start: str = ""
    end: str = ""
    sort: str = "name"
    # The browser's IANA time zone, which the page script fills (#558): Start
    # and End are whole days there.
    zone: str = ""
    page: int = 1
    # Rows per page (web/tables.py PAGE_SIZES). The selection takes any
    # LIMIT; "All" is not offered, so a page view stays bounded.
    size: str = str(PAGE_SIZE)

    @classmethod
    def parse(cls, parameters, *, detail=False):
        """Reject unknown/repeated values and filters inapplicable to summaries."""
        if not hasattr(parameters, "getlist"):
            if not isinstance(parameters, dict) or any(
                type(value) is not str for value in parameters.values()
            ):
                raise ValueError("Ministry filters require text values.")
            parameters = MultiValueDict(
                {key: [value] for key, value in parameters.items()}
            )
        values = filters(parameters, allowed=set(cls.__dataclass_fields__))
        query = cls(**(values | {"page": parse_page(values.get("page", "1"))}))
        bounded_text(query.search)
        if (
            query.history not in {"current", "all"}
            or query.state not in STATES
            or query.sort not in SUMMARY_TOKENS | {"newest", "oldest"}
            or query.size not in {str(size) for size in PAGE_SIZES}
            or (
                not detail
                and (
                    query.history != "current"
                    or query.state != "any"
                    or query.start
                    or query.end
                    or query.sort not in SUMMARY_TOKENS
                )
            )
        ):
            raise ValueError("Invalid Ministry filters.")
        for value in (query.start, query.end):
            if value and date.fromisoformat(value).isoformat() != value:
                raise ValueError("Invalid Ministry date filter.")
        if query.start and query.end and query.start > query.end:
            raise ValueError("Invalid Ministry date interval.")
        if query.zone not in timezone_names():
            # A date needs the browser's zone (a blank one from a tab opened
            # before #558, or one outside the catalog, is never guessed).
            # Without dates the zone is unused, and an unknown one is dropped.
            if query.start or query.end:
                raise UnknownZone("Ministry date filters need the browser's zone.")
            query = replace(query, zone="")
        return query

    def form_values(self):
        """Keep applied selection through native pagination without query strings.

        Page and size choose only the screen's window, so they are left out.
        The SQL selection and an export capture take ``selection_filters``.
        """
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key not in {"page", "size"}
        }

    def selection_filters(self):
        """The ``filters`` the SQL selection and an export capture accept.

        The frozen selection (``stewardship_ministry_report_v3``) and the
        export capture guard still take an ``activity`` filter, whose
        neutral "any" selects every Ministry. The filter itself is gone
        (#1267), so always send "any", as Ministry follow-up sends its
        retired assignee filter (``ministry_followup.selection_filters``).
        """
        return self.form_values() | {"activity": "any"}

    @property
    def page_size(self):
        """The validated rows-per-page choice as an integer."""
        return int(self.size)


def reads_every_ministry(principal):
    """Whether ``principal`` reads every Ministry: Staff or an Administrator.

    Only they see whether a Ministry is still in ParishSoft's data and in
    the campaign. Those are the Administrator's concerns and change nothing
    a Ministry leader does with a request, so a leader's pages leave them
    out (#1135).
    """
    return allows(principal, Capability.MINISTRY_REPORT)


def can_report(principal):
    """Allow global operational roles or a leader with at least one current scope."""
    return allows(principal, Capability.MINISTRY_REPORT) or any(
        allows(principal, Capability.MINISTRY_REPORT, ministry_id=duid)
        for duid in getattr(principal, "ministries", ())
    )


def unscoped(principal, message):
    """The refusal for a reader no Ministry page admits (#939).

    A Ministry leader admitted by a login rule may lead no Ministry in the
    current ParishSoft data (no leader role on a current campaign
    Ministry's roster). That person is told so plainly, still with a 403,
    rather than given the generic sign-in denial, which suggests signing in
    again would help. No current session (``None``) gets the signed-out
    refusal, whose page load goes to the sign-in page (#2062). Anyone else
    keeps the generic denial (``message`` is internal and never shown).
    """
    if principal is None:
        return signed_out()
    if "ministry_leader" in getattr(principal, "roles", ()):
        return UserFacingDenied(
            _(
                "You don't lead any Ministry in the current ParishSoft data, "
                "so there are no Ministry pages to show."
            )
        )
    return PermissionError(message)


def campaign_ids(principal):
    """Discover only readable, Ministry-enabled campaign identities in scope.

    Labels remain behind the response guard. This same predicate controls both
    the default redirect and explicit selection, never the global pointer.
    The scope is the actor's current one in SQL (stewardship_ministry_scope_v1,
    as the report itself reads it), narrowed by Python's view of the same actor
    so that neither can widen the other (#389 L3).
    """
    if not can_report(principal):
        raise PermissionError("Ministry report access is unavailable.")
    with connection.cursor() as cursor:
        cursor.execute(
            """WITH scope AS MATERIALIZED (
                SELECT coalesce(q.s->'operational'='true'::jsonb,false)
                        AND %s AS operational,
                    ARRAY(SELECT m.value::bigint FROM jsonb_array_elements_text(
                        coalesce(q.s->'ministries','[]'::jsonb)) m(value)
                        WHERE m.value::bigint=ANY(%s::bigint[])) AS ministries
                FROM (SELECT stewardship_ministry_scope_v1(%s) AS s) q
            )
            SELECT c.id FROM stewardship_campaign c
            CROSS JOIN scope p
            JOIN stewardship_campaign_configuration cc
                ON cc.id=c.active_configuration_id
            LEFT JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id
            LEFT JOIN stewardship_source_current sc ON sc.singleton
            JOIN stewardship_source_snapshot ss ON ss.id=CASE
                WHEN c.state='archived' THEN k.source_snapshot_id
                ELSE sc.snapshot_id END
            WHERE c.state IN ('draft','scheduled','active','closed','archived')
                AND cc.values->'modules' ? 'ministry'
                AND ss.state='promoted' AND ss.compacted_at IS NULL
                AND (p.operational OR EXISTS(SELECT 1
                    FROM jsonb_array_elements_text(cc.values->'ministry_duids') n
                    WHERE n::bigint BETWEEN 1 AND 2147483647
                        AND n::bigint=ANY(p.ministries))
                    -- A Ministry removed from a live campaign keeps its
                    -- current (not withdrawn) requests in the report, as the
                    -- report SQL does (#342), by each request's Ministry in
                    -- force: the one an Edit carried it into (#2331).
                    OR EXISTS(SELECT 1 FROM stewardship_submission s
                    JOIN stewardship_ministry_request r ON r.submission_id=s.id
                    WHERE s.campaign_id=c.id AND s.mode='live'
                        AND r.state NOT IN ('cancelled','superseded')
                        AND coalesce(r.reviewed_ministry_duid,r.ministry_duid)
                            =ANY(p.ministries)))
            ORDER BY c.created_at DESC,c.id""",
            [
                allows(principal, Capability.MINISTRY_REPORT),
                sorted(value for value in principal.ministries if value < 2**31),
                principal.identity,
            ],
        )
        return tuple(row[0] for row in cursor.fetchall())


def within_principal(principal, capability, operational, ministries):
    """Cross-check SQL's scope against Python's view of the same actor.

    SQL derives the scope from the actor (stewardship_ministry_scope_v1) and
    is authoritative. A scope wider than Python's (operational when Python
    is not, or a Ministry Python does not hold) means the two have drifted,
    so the page is refused rather than shown.
    """
    if allows(principal, capability):
        return True
    return not operational and all(
        allows(principal, capability, ministry_id=duid) for duid in ministries
    )


def ministry_page(
    campaign_id, query, principal, *, ministry_id=None, action="join", every=False
):
    """Read under the response guard using its freshly resolved actor, not a cookie.

    SQL derives the actor's current role scope (stewardship_ministry_report_v4,
    #389 L3) and intersects it with campaign selection before reading
    requests; Python's scope is a pre-check and a cross-check. Contact
    redaction occurs inside the query, before detached values reach the
    renderer. No credential or financial columns are selected.

    ``every`` reads the whole selection rather than one page of it: the
    whole Ministry summary (the packet's choices, #533), or every request
    row of one Ministry and kind, which the follow-up request page and the
    queue's export read their contact details from (#1157).
    """
    if not can_report(principal) or (
        ministry_id is not None
        and (
            type(ministry_id) is not int
            or not 0 < ministry_id < 2**31
            or not allows(
                principal, Capability.MINISTRY_REPORT, ministry_id=ministry_id
            )
        )
    ):
        raise PermissionError("Ministry report access is unavailable.")
    if action not in {"join", "leave"}:
        raise ValueError("Invalid Ministry action.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_ministry_report_v4("
            "campaign_uuid => %s, filters => %s::jsonb, actor_uuid => %s, "
            "ministry_id => %s, request_action => %s, "
            "page_limit => %s, page_offset => %s)::text",
            [
                campaign_id,
                json.dumps(query.selection_filters()),
                principal.identity,
                ministry_id,
                action,
                # A NULL limit selects every summary row.
                None if every else query.page_size,
                0 if every else (query.page - 1) * query.page_size,
            ],
        )
        value = cursor.fetchone()
    if value is None or value[0] is None:
        raise ReadUnavailable("Ministry report inputs are unavailable.")
    result = json.loads(value[0])
    if result.get("disabled"):
        raise PermissionError("Ministry reporting is not enabled for this campaign.")
    if result.get("unavailable"):
        raise ReadUnavailable("Ministry report inputs are unavailable.")
    scope = result["authorization_scope"]
    # Only an actor SQL itself treats as operational may read an unauthorized
    # (empty) summary: a campaign with no Ministries. Anyone else, including
    # a principal Python treats as Admin or Staff whom SQL does not (a drift
    # that would otherwise show a silently empty page), is refused, as the
    # follow-up cross-check refuses (#389 L3).
    if (
        not result["authorized"]
        and (ministry_id is not None or not scope["operational"])
    ) or not within_principal(
        principal, Capability.MINISTRY_REPORT, scope["operational"], scope["ministries"]
    ):
        raise PermissionError("Ministry report access is unavailable.")
    if ministry_id is not None and not result["summaries"]:
        # A filter can legitimately hide a selected Ministry; never turn an
        # empty result into a broader fallback scope.
        result["rows"] = []
    for row in result["rows"]:
        row["submitted_at"] = datetime.fromisoformat(row["submitted_at"])
        row["state_label"] = STATE_LABELS[row["state"]]
        # Every row is of the chosen kind (the selection filters on it).
        row["outcome_label"] = outcome_label(row["outcome"], action, "Not yet recorded")
        row["address_lines"] = address_lines(row["address"] or {})
        # An existing Member's gender is ParishSoft's code (#1621).
        row["gender"] = gender_word(row.get("gender"))
        row["emails"] = [
            entry["value"] for entry in row["emails"] or [] if entry.get("value")
        ]
        row["phones"] = {
            key: value for key, value in (row["phones"] or {}).items() if value
        }
    for key in ("source_as_of", "observed_at"):
        result["metadata"][key] = datetime.fromisoformat(result["metadata"][key])
    return result
