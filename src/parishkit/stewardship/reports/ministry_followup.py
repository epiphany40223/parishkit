"""Bounded, scoped Ministry follow-up queue, detail and history reads."""

import json
from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import ObjectDoesNotExist
from django.db import connection
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import PageWindow, filters
from parishkit.stewardship.web.tables import PAGE_SIZES, Sorting
from parishkit.stewardship.workflows.models import (
    RESOLVED_OUTCOMES,
    MinistryWorkflowRevision,
)

from .information import parse_page

PAGE_SIZE = 50
# The installed selection (schema/ministry_followup.sql) orders and pages the
# queue, so a heading can only choose one of its existing sort values; the
# schema is frozen for v1. Member and Ministry sort A-Z only (the selection
# has no Z-A order for them), and Request sorts by submission time. Status
# and Last contact are not sortable: the selection has no order for them.
SORTING = Sorting(
    {
        "newest": ("request", True),
        "oldest": ("request", False),
        "name": ("member", False),
        "ministry": ("ministry", False),
    },
    "newest",
)
# Current statuses, offered as filters. Follow-up has no assignee (#552); a
# request still stored as `assigned` from before then reads as New
# (followup_page), because `assigned` only ever meant "has an assignee".
STATES = {
    "new": "New",
    "in_progress": "In progress",
    "resolved": "Resolved",
    "closed_no_response": "Closed: no response",
    "cancelled": "Withdrawn by Family",
    "superseded": "Replaced by Family",
}
# History shows what each past edit recorded, including an old assignment.
HISTORY_STATES = STATES | {"assigned": "Assigned"}
# The exact labels of the multi-Ministry packet, so screens and packets agree.
OUTCOMES = {
    "joined": "Joined ministry",
    "leave_confirmed": "Left ministry",
    "declined": "Declined / no longer interested",
    "no_response": "No response",
    "duplicate": "Duplicate request",
    "other": "Other",
}
CHANNELS = {
    "email": "Email",
    "phone": "Phone",
    "in_person": "In person",
    "other": "Other",
}


@dataclass(frozen=True, repr=False)
class FollowupQuery:
    """Search is private POST state, never a query-string or audit payload."""

    search: str = ""
    ministry: str = ""
    action: str = "any"
    state: str = "unresolved"
    outcome: str = "any"
    history: str = "current"
    sort: str = "newest"
    page: int = 1
    # Rows per page (web/tables.py PAGE_SIZES); the selection takes any LIMIT.
    size: str = str(PAGE_SIZE)

    @classmethod
    def parse(cls, parameters):
        """Accept only single bounded values from closed vocabularies."""
        if not hasattr(parameters, "getlist"):
            if not isinstance(parameters, dict) or any(
                type(value) is not str for value in parameters.values()
            ):
                raise ValueError("Follow-up filters require text values.")
            parameters = MultiValueDict(
                {key: [value] for key, value in parameters.items()}
            )
        values = filters(parameters, allowed=set(cls.__dataclass_fields__))
        query = cls(**(values | {"page": parse_page(values.get("page", "1"))}))
        if (
            query.action not in {"any", "join", "leave"}
            or query.state not in {"any", "unresolved", *STATES}
            or query.outcome not in {"any", *RESOLVED_OUTCOMES, "no_response"}
            or query.history not in {"current", "all"}
            or query.sort not in SORTING.tokens
            or query.size not in {str(size) for size in PAGE_SIZES}
            or (query.ministry and not valid_ministry(query.ministry))
        ):
            raise ValueError("Invalid follow-up filters.")
        bounded_text(query.search)
        return query

    def form_values(self):
        """The filters and sort the selection reads; page and size only window it."""
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key not in {"page", "size"}
        }

    @property
    def page_size(self):
        """The validated rows-per-page choice as an integer."""
        return int(self.size)


def valid_ministry(value):
    """One canonical positive signed-32-bit DUID spelling, as source rows use."""
    return (
        value.isascii()
        and value.isdecimal()
        and str(int(value)) == value
        and 0 < int(value) < 2**31
    )


def can_follow_up(principal):
    """Allow global operational roles or a leader with at least one current scope."""
    return allows(principal, Capability.MINISTRY_FOLLOWUP) or any(
        allows(principal, Capability.MINISTRY_FOLLOWUP, ministry_id=duid)
        for duid in getattr(principal, "ministries", ())
    )


def followup_page(campaign_id, query, principal, *, request_id=None):
    """Read one coherent page under the response guard's freshly resolved actor.

    SQL intersects the current role scope with the campaign's Ministries before
    reading any request, so an out-of-scope request UUID yields no row rather
    than a different error. No contact, address or financial column is selected.
    """
    if not can_follow_up(principal):
        raise PermissionError("Ministry follow-up access is unavailable.")
    with connection.cursor() as cursor:
        # SQL derives the role scope from the actor's current rules, as the
        # export capture does (#389 L3); the actor is also the viewer.
        cursor.execute(
            "SELECT stewardship_ministry_followup_for_v1("
            "actor => %s, campaign_uuid => %s, filters => %s::jsonb, "
            "request_uuid => %s, page_limit => %s, page_offset => %s)::text",
            [
                principal.identity,
                campaign_id,
                # The frozen selection still reads an assignee filter
                # (schema/ministry_followup.sql); without one every row
                # fails it, so always send the neutral "any" (#552).
                json.dumps(query.form_values() | {"assignee": "any"}),
                request_id,
                query.page_size,
                (query.page - 1) * query.page_size,
            ],
        )
        value = cursor.fetchone()
    if value is None or value[0] is None:
        raise ReadUnavailable("Ministry follow-up inputs are unavailable.")
    result = json.loads(value[0])
    if result.get("disabled"):
        raise PermissionError("Ministry follow-up is not enabled for this campaign.")
    if result.get("unavailable"):
        raise ReadUnavailable("Ministry follow-up inputs are unavailable.")
    if not result["authorized"]:
        raise PermissionError("Ministry follow-up access is unavailable.")
    if request_id is not None and not result["rows"]:
        raise ObjectDoesNotExist("Ministry request is unavailable.")
    for row in result["rows"]:
        # A request assigned before assignment was removed (#552) reads, and
        # is edited, as New; its next save stores New with no assignee.
        if row["state"] == "assigned":
            row["state"] = "new"
        row.pop("assignee_id", None)
        row["state_label"] = STATES[row["state"]]
        row["outcome_label"] = OUTCOMES.get(row["outcome"], "")
        for field in (
            "submitted_at",
            "resolved_at",
            "email_contact_at",
            "phone_contact_at",
            "last_contact_at",
        ):
            row[field] = datetime.fromisoformat(row[field]) if row[field] else None
    # JSON carries instants as text, and Django's date filter renders text as
    # nothing at all rather than failing. Only this one is displayed.
    result["metadata"]["source_as_of"] = datetime.fromisoformat(
        result["metadata"]["source_as_of"]
    )
    return result


def followup_history(request_id, page):
    """Bounded Staff history across same-intent Family resubmissions, newest first.

    A raw queryset cannot be sliced without loading every row, so the window and
    its one has-next sentinel row are bound in SQL.
    """
    window = PageWindow(page=page)
    rows = list(
        MinistryWorkflowRevision.objects.raw(
            "SELECT r.* FROM stewardship_ministry_workflow_chain_v1(%s) c "
            "JOIN stewardship_ministry_revision r ON r.request_id=c.request_id "
            "ORDER BY c.depth,r.expected_version DESC LIMIT %s OFFSET %s",
            [request_id, window.size + 1, (page - 1) * window.size],
        )
    )
    return rows[: window.size], len(rows) > window.size
