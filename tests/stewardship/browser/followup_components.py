"""Ministry follow-up reuses the shared browser server and production templates."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.ministry_followup import (
    CHANNELS,
    OUTCOMES,
    SORTING,
    STATES,
    FollowupQuery,
)
from parishkit.stewardship.web.tables import report_table
from parishkit.stewardship.workflows.models import STAFF_STATES

CAMPAIGN = UUID(int=92)
# The bulk assignment's form action. The fixture server answers a POST here
# as the view does after a successful assignment (#518): a 302 back to the
# filtered queue at its table, a redirect Playwright cannot fulfil from a
# route on every engine.
ASSIGN = f"/admin/reports/{CAMPAIGN}/ministries/follow-up/assign"
ASSIGNED = "/followup-assigned#table"


def _table(rows, query, total, campaign):
    """The shared POST navigator/heading model the queue view builds (#203)."""
    return report_table(
        rows,
        number=1,
        size=50,
        total=total,
        carry=[(k, v) for k, v in query.form_values().items() if k != "sort"],
        sorting=SORTING,
        sort=query.sort,
        action=f"/admin/reports/{campaign}/ministries/follow-up/",
    )


def _queue_state(query):
    """The bulk form's hidden `queue-` view fields the queue view renders (#518)."""
    view = query.form_values() | {"page": str(query.page), "size": query.size}
    return [(f"queue-{key}", value) for key, value in view.items()]


def components(context, admin):
    """Detached authorized sample data exercises native controls and escaping."""
    campaign, request, leader = CAMPAIGN, UUID(int=93), UUID(int=94)
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
        table=_table([row], query, 51, campaign),
        mutable=True,
        item=None,
        history=[],
        previous_history=None,
        next_history=None,
        request_key=UUID(int=95),
        assignees=[SimpleNamespace(id=leader, email="leader@example.org")],
        bulk_ministry=9,
        queue_state=_queue_state(query),
        states=STATES,
        staff_states=[(key, STATES[key]) for key in STAFF_STATES],
        outcomes=OUTCOMES,
        channels=CHANNELS,
        viewer=str(leader),
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
    # The queue a bulk assignment redirects back to: the same filters, with
    # the row reassigned at its next version. The new assignee's label
    # differs from every label on the queue page, so a test that finds it
    # knows the table was swapped in from the redirected page.
    assigned = row | dict(
        version=4, assignee_id=str(request), assignee_label="other@example.org"
    )
    pages = {
        "/followup-queue": values,
        "/followup-assigned": values
        | dict(rows=[assigned], table=_table([assigned], query, 51, campaign)),
        "/followup-all": values
        | dict(
            query=unfiltered,
            table=_table([row], unfiltered, 51, campaign),
            bulk_ministry=None,
            assignees=[],
        ),
        "/followup-empty": values
        | dict(rows=[], total=0, table=_table([], query, 0, campaign)),
        "/followup-gated": values | dict(mutable=False, assignees=[]),
        "/followup-item": values
        | dict(item=row, history=[revision], next_history=2, bulk_ministry=None),
        "/followup-closed": values
        | dict(item=closed, rows=[closed], history=[revision], bulk_ministry=None),
        "/followup-item-stale": values
        | dict(item=row, stale_assignee=True, assignees=[], bulk_ministry=None),
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
