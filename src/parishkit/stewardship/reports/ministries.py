"""Closed Ministry filters and a privacy-safe report projection."""

import json
from dataclasses import dataclass
from datetime import date, datetime

from django.db import connection
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.campaigns.domain import Percentage
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.presentation import out_of
from parishkit.stewardship.web.tables import PAGE_SIZES, Sorting

from .directories import address_lines
from .information import parse_page

PAGE_SIZE = 50
# The installed selection (schema/ministry_reports.sql) orders and pages the
# rows, so a heading can only choose one of its existing sort values; the
# schema is frozen for v1. The summary sorts by Ministry name only; Joining,
# Leaving, Unresolved and Follow-up progress are not sortable because the
# selection has no order for them. Detail rows sort by Member name or by
# submission time; the status and contact columns have no selection order.
SUMMARY_SORTING = Sorting(
    {"name": ("ministry", False), "name_desc": ("ministry", True)}, "name"
)
DETAIL_SORTING = Sorting(
    {
        "name": ("member", False),
        "name_desc": ("member", True),
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
# The frozen report selection (schema/ministry_reports.sql) still returns each
# row's assignee email; nothing renders it any more.
STATE_LABELS = STATES | {"assigned": STATES["new"]}
# A Ministry an Administrator removed from a live campaign whose requests are
# kept (#342); SQL marks it in_campaign false.
NOT_IN_CAMPAIGN = "No longer in this campaign"
OUTCOMES = {
    "joined": "Joined ministry",
    "leave_confirmed": "Left ministry",
    "declined": "Declined / no longer interested",
    "no_response": "No response",
    "duplicate": "Duplicate request",
    "other": "Other",
}


@dataclass(frozen=True, repr=False)
class MinistryQuery:
    """Private search/date state travels only in bounded CSRF POST forms."""

    search: str = ""
    activity: str = "any"
    history: str = "current"
    state: str = "any"
    start: str = ""
    end: str = ""
    sort: str = "name"
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
            query.activity not in {"any", "active", "inactive", "unavailable"}
            or query.history not in {"current", "all"}
            or query.state not in STATES
            or query.sort not in {"name", "name_desc", "newest", "oldest"}
            or query.size not in {str(size) for size in PAGE_SIZES}
            or (
                not detail
                and (
                    query.history != "current"
                    or query.state != "any"
                    or query.start
                    or query.end
                    or query.sort not in {"name", "name_desc"}
                )
            )
        ):
            raise ValueError("Invalid Ministry filters.")
        for value in (query.start, query.end):
            if value and date.fromisoformat(value).isoformat() != value:
                raise ValueError("Invalid Ministry date filter.")
        if query.start and query.end and query.start > query.end:
            raise ValueError("Invalid Ministry date interval.")
        return query

    def form_values(self):
        """Keep applied selection through native pagination without query strings.

        Page and size choose only the screen's window, so they are left out:
        the SQL filters and an export capture accept exactly these keys.
        """
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key not in {"page", "size"}
        }

    @property
    def page_size(self):
        """The validated rows-per-page choice as an integer."""
        return int(self.size)


def can_report(principal):
    """Allow global operational roles or a leader with at least one current scope."""
    return allows(principal, Capability.MINISTRY_REPORT) or any(
        allows(principal, Capability.MINISTRY_REPORT, ministry_id=duid)
        for duid in getattr(principal, "ministries", ())
    )


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
                    -- report SQL does (#342).
                    OR EXISTS(SELECT 1 FROM stewardship_submission s
                    JOIN stewardship_ministry_request r ON r.submission_id=s.id
                    WHERE s.campaign_id=c.id AND s.mode='live'
                        AND r.state NOT IN ('cancelled','superseded')
                        AND r.ministry_duid=ANY(p.ministries)))
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


def ministry_page(campaign_id, query, principal, *, ministry_id=None, action="join"):
    """Read under the response guard using its freshly resolved actor, not a cookie.

    SQL derives the actor's current role scope (stewardship_ministry_report_v2,
    #389 L3) and intersects it with campaign selection before reading
    requests; Python's scope is a pre-check and a cross-check. Contact
    redaction occurs inside the query, before detached values reach the
    renderer. No credential or financial columns are selected.
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
            "SELECT stewardship_ministry_report_v2("
            "campaign_uuid => %s, filters => %s::jsonb, actor_uuid => %s, "
            "ministry_id => %s, request_action => %s, "
            "page_limit => %s, page_offset => %s)::text",
            [
                campaign_id,
                json.dumps(query.form_values()),
                principal.identity,
                ministry_id,
                action,
                query.page_size,
                (query.page - 1) * query.page_size,
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
        # A changed activity filter can legitimately hide a selected Ministry;
        # never turn an empty result into a broader fallback scope.
        result["rows"] = []
    for summary in result["summaries"]:
        summary["progress"] = out_of(
            Percentage(summary["completed"], summary["requests"])
        )
    for row in result["rows"]:
        row["submitted_at"] = datetime.fromisoformat(row["submitted_at"])
        row["state_label"] = STATE_LABELS[row["state"]]
        row["outcome_label"] = OUTCOMES.get(row["outcome"], "Not yet recorded")
        row["address_lines"] = address_lines(row["address"] or {})
        row["emails"] = [
            entry["value"] for entry in row["emails"] or [] if entry.get("value")
        ]
        row["phones"] = {
            key: value for key, value in (row["phones"] or {}).items() if value
        }
    for key in ("source_as_of", "observed_at"):
        result["metadata"][key] = datetime.fromisoformat(result["metadata"][key])
    return result
