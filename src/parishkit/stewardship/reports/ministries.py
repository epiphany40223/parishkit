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

from .directories import address_lines
from .information import parse_page
from .ministry_queries import MINISTRY_REPORT

PAGE_SIZE = 50
STATES = {
    "any": "All states",
    "unresolved": "Unresolved",
    "new": "New",
    "assigned": "Assigned",
    "in_progress": "In progress",
    "resolved": "Resolved",
    "closed_no_response": "Closed without response",
    "cancelled": "Cancelled",
    "superseded": "Superseded",
}
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
        """Keep applied selection through native pagination without query strings."""
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key != "page"
        }


def can_report(principal):
    """Allow global operational roles or a leader with at least one current scope."""
    return allows(principal, Capability.MINISTRY_REPORT) or any(
        allows(principal, Capability.MINISTRY_REPORT, ministry_id=duid)
        for duid in getattr(principal, "ministries", ())
    )


def ministry_page(campaign_id, query, principal, *, ministry_id=None, action="join"):
    """Read under the response guard using its freshly resolved actor, not a cookie.

    SQL intersects the server's current role scope with campaign selection before
    reading requests. Contact redaction occurs inside the query, before detached
    values reach the renderer. No credential or financial columns are selected.
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
    parameters = query.form_values() | {
        "campaign": campaign_id,
        "operational": allows(principal, Capability.MINISTRY_REPORT),
        "scope": sorted(value for value in principal.ministries if value < 2**31),
        "ministry": ministry_id,
        "action": action,
        "history": query.history == "all",
        "limit": PAGE_SIZE,
        "offset": (query.page - 1) * PAGE_SIZE,
    }
    with connection.cursor() as cursor:
        cursor.execute(MINISTRY_REPORT, parameters)
        value = cursor.fetchone()
    if value is None:
        raise ReadUnavailable("Ministry report inputs are unavailable.")
    result = json.loads(value[0])
    if ministry_id is not None and not result["authorized"]:
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
        row["state_label"] = STATES[row["state"]]
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
