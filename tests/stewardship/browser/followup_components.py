"""Ministry follow-up reuses the shared browser server and production templates."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.reports.ministry_followup import (
    CHANNELS,
    HISTORY_STATES,
    OUTCOMES,
    SORTING,
    STATES,
    FollowupQuery,
)
from parishkit.stewardship.web.tables import report_table
from parishkit.stewardship.workflows.followup import outcomes_for
from parishkit.stewardship.workflows.models import STAFF_STATES


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


def components(context, admin):
    """Detached authorized sample data exercises native controls and escaping."""
    campaign, request = UUID(int=92), UUID(int=93)
    moment = datetime(2026, 9, 19, 15, 4, tzinfo=UTC)
    row = dict(
        id=str(request),
        version=3,
        ministry_duid=9,
        ministry_name="Example <Ministry>",
        member_name="Example <Member>",
        action="join",
        state="new",
        state_label=STATES["new"],
        outcome=None,
        outcome_label="",
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
    # A past edit recorded before assignment was removed (#552) keeps
    # showing the assignment it recorded.
    revision = SimpleNamespace(
        actor_label="leader@example.org",
        assignee_label="leader@example.org",
        created_at=moment,
        state_label=HISTORY_STATES["assigned"],
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
        states=STATES,
        staff_states=[(key, STATES[key]) for key in STAFF_STATES],
        resolved_outcomes=[(key, OUTCOMES[key]) for key in outcomes_for("join")],
        outcomes=OUTCOMES,
        channels=CHANNELS,
        total=51,
        rows=[row],
        ministries=[dict(duid=9, name="Example <Ministry>")],
        metadata=dict(name="Sample campaign", source_as_of=moment, timezone="UTC"),
    )
    # The form's values, as the view builds them (or keeps them on a refusal).
    form = dict(
        expected_version=str(row["version"]),
        state=row["state"],
        outcome="",
        notes=row["notes"],
        contact_channel="",
        contact_date="",
        contact_time="",
        contact_notes="",
    )
    leave = row | dict(action="leave", member_name="Leaving <Member>")
    refused = form | dict(
        state="resolved",
        outcome="other",
        notes="Kept <note>",
        contact_channel="phone",
        contact_date="2026-09-19",
        contact_time="15:04",
        contact_notes="Kept <reply>",
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
            table=_table([row], unfiltered, 51, campaign),
        ),
        "/followup-empty": values
        | dict(rows=[], total=0, table=_table([], query, 0, campaign)),
        "/followup-gated": values | dict(mutable=False),
        "/followup-item": values
        | dict(item=row, form=form, history=[revision], next_history=2),
        "/followup-item-leave": values
        | dict(
            item=leave,
            form=form,
            resolved_outcomes=[(key, OUTCOMES[key]) for key in outcomes_for("leave")],
        ),
        "/followup-item-refused": values
        | dict(
            item=row,
            form=refused,
            errors=[
                dict(
                    message="Left ministry doesn't apply to a request to join.",
                    field_id="followup-outcome",
                )
            ],
        ),
        "/followup-closed": values
        | dict(item=closed, rows=[closed], history=[revision]),
        "/followup-item-gated": values | dict(item=row, form=form, mutable=False),
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
