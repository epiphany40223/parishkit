"""Bounded, scoped Ministry follow-up queue, detail and history reads."""

import json
import re
from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import ObjectDoesNotExist
from django.db import connection
from django.utils.datastructures import MultiValueDict

from parishkit.stewardship.accounts.policy import Capability, allows
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.web.content import bounded_text
from parishkit.stewardship.web.contracts import PageWindow, filters
from parishkit.stewardship.workflows.models import (
    RESOLVED_OUTCOMES,
    MinistryWorkflowRevision,
)

from .information import parse_page

PAGE_SIZE = 50
STATES = {
    "new": "New",
    "assigned": "Assigned",
    "in_progress": "In progress",
    "resolved": "Resolved",
    "closed_no_response": "Closed: no response",
    "cancelled": "Withdrawn by Family",
    "superseded": "Replaced by Family",
}
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
UUID_TEXT = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}")


@dataclass(frozen=True, repr=False)
class FollowupQuery:
    """Search is private POST state, never a query-string or audit payload."""

    search: str = ""
    ministry: str = ""
    action: str = "any"
    state: str = "unresolved"
    outcome: str = "any"
    assignee: str = "any"
    history: str = "current"
    sort: str = "newest"
    page: int = 1

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
            or query.sort not in {"newest", "oldest", "name", "ministry"}
            or (query.ministry and not valid_ministry(query.ministry))
            or (
                query.assignee not in {"any", "mine", "unassigned"}
                and UUID_TEXT.fullmatch(query.assignee) is None
            )
        ):
            raise ValueError("Invalid follow-up filters.")
        bounded_text(query.search)
        return query

    def form_values(self):
        """Return escaped-by-template values for CSRF-protected page navigation."""
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
            if key != "page"
        }


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
        cursor.execute(
            "SELECT stewardship_ministry_followup_v1("
            "campaign_uuid => %s, filters => %s::jsonb, operational => %s, "
            "ministry_scope => %s::bigint[], viewer => %s, request_uuid => %s, "
            "page_limit => %s, page_offset => %s)::text",
            [
                campaign_id,
                json.dumps(query.form_values()),
                allows(principal, Capability.MINISTRY_FOLLOWUP),
                sorted(value for value in principal.ministries if value < 2**31),
                principal.identity,
                request_id,
                PAGE_SIZE,
                (query.page - 1) * PAGE_SIZE,
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
    # nothing at all rather than failing.
    for field in ("source_as_of", "observed_at"):
        result["metadata"][field] = datetime.fromisoformat(result["metadata"][field])
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


def assignable(ministry_duid):
    """Active users who currently hold follow-up authority for this Ministry."""
    return list(
        PortalUser.objects.raw(
            "SELECT id,email FROM stewardship_portal_user u WHERE NOT u.disabled "
            "AND stewardship_ministry_followup_authorized_v1(u.id,%s) "
            "ORDER BY lower(u.email),u.id",
            [ministry_duid],
        )
    )
