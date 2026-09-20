"""Ministry follow-up reuses the shared browser server and production templates."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.ministry_followup import (
    CHANNELS,
    OUTCOMES,
    STATES,
    FollowupQuery,
)
from parishkit.stewardship.workflows.models import STAFF_STATES


def components(context, admin):
    """Detached authorized sample data exercises native controls and escaping."""
    campaign, request, leader = UUID(int=92), UUID(int=93), UUID(int=94)
    moment = datetime(2026, 9, 19, 15, 4, tzinfo=UTC)
    row = dict(
        id=str(request),
        version=3,
        ministry_duid=9,
        ministry_name="Example <Ministry>",
        member_name="Example <Member>",
        action="join",
        state="assigned",
        state_label=STATES["assigned"],
        outcome=None,
        outcome_label="",
        assignee_id=str(leader),
        assignee_label="leader@example.org",
        submitted_at=moment,
        resolved_at=None,
        source_resolved=False,
        latest=True,
        open=True,
        notes="Left a <private> voicemail",
        email_contact_at=None,
        phone_contact_at=moment,
        last_contact_at=moment,
    )
    revision = SimpleNamespace(
        actor_label="leader@example.org",
        assignee_label="leader@example.org",
        created_at=moment,
        state_label=STATES["assigned"],
        outcome_label="",
        channel_label=CHANNELS["phone"],
        contact_at=moment,
        contact_notes="No <answer>",
        notes="Left a <private> voicemail",
    )
    query = FollowupQuery(search="Example", ministry="9")
    values = dict(
        campaign_id=campaign,
        query=query,
        query_fields=query.form_values(),
        mutable=True,
        item=None,
        history=[],
        previous_history=None,
        next_history=None,
        request_key=UUID(int=95),
        assignees=[SimpleNamespace(id=leader, email="leader@example.org")],
        bulk_ministry=9,
        states=STATES,
        staff_states=[(key, STATES[key]) for key in STAFF_STATES],
        outcomes=OUTCOMES,
        channels=CHANNELS,
        viewer=str(leader),
        previous_page=None,
        next_page=2,
        total=51,
        rows=[row],
        ministries=[dict(duid=9, name="Example <Ministry>")],
        metadata=dict(name="Sample campaign", source_as_of=moment, timezone="UTC"),
    )
    unfiltered = FollowupQuery()
    closed = row | dict(
        open=False,
        state="resolved",
        state_label=STATES["resolved"],
        outcome="joined",
        outcome_label=OUTCOMES["joined"],
    )
    pages = {
        "/followup-queue": values,
        "/followup-all": values
        | dict(
            query=unfiltered,
            query_fields=unfiltered.form_values(),
            bulk_ministry=None,
            assignees=[],
        ),
        "/followup-empty": values | dict(rows=[], total=0, next_page=None),
        "/followup-gated": values | dict(mutable=False, assignees=[]),
        "/followup-item": values
        | dict(item=row, history=[revision], next_history=2, bulk_ministry=None),
        "/followup-closed": values
        | dict(item=closed, rows=[closed], history=[revision], bulk_ministry=None),
        "/followup-item-gated": values
        | dict(item=row, mutable=False, assignees=[], bulk_ministry=None),
    }
    result = {
        path: (
            "text/html",
            render_to_string(
                "stewardship/ministry-followup.html",
                context | {"admin_chrome": admin} | data,
            ),
        )
        for path, data in pages.items()
    }
    for status in (400, 409, 503):
        result[f"/followup-error-{status}"] = (
            "text/html",
            render_to_string(
                "stewardship/ministry-followup-error.html",
                context
                | {"admin_chrome": admin}
                | dict(campaign_id=campaign, request_id=request, status=status),
            ),
        )
    return result
