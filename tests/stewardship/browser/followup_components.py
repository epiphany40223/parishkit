"""Ministry follow-up reuses the shared browser server and production templates.

The request page is also served at its real address (``ITEM``), with a
"saved" view (``SAVED``), and the fixture server answers its Save POST with
a real Post/Redirect/Get redirect (``POSTS``). The in-place mechanism (#519)
follows a save only back to the same path, and Playwright cannot fulfil a
redirect from a route on every engine. Two more requests answer a save with
the request closed (``RESOLVED``: no form comes back) and with follow-up
edits unavailable (``GATED``: Save comes back disabled). The history's
second page (``OLDER``) and first page by number (``NEWER``) serve the
in-place history pager tests (#519 PR 6).
"""

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
from parishkit.stewardship.reports.ministry_followup_views import (
    REFUSALS,
    _refusal_error,
)
from parishkit.stewardship.web.tables import report_table
from parishkit.stewardship.workflows.followup import FollowupRefusal, outcomes_for
from parishkit.stewardship.workflows.models import STAFF_STATES

CAMPAIGN, REQUEST = UUID(int=92), UUID(int=93)
ITEM = f"/admin/reports/{CAMPAIGN}/ministries/follow-up/{REQUEST}/"
UPDATE = ITEM + "update"
SAVED = ITEM + "?saved=1"
OLDER, NEWER = ITEM + "?page=2", ITEM + "?page=1"
RESOLVE_REQUEST, GATE_REQUEST = UUID(int=94), UUID(int=96)
RESOLVE_ITEM = f"/admin/reports/{CAMPAIGN}/ministries/follow-up/{RESOLVE_REQUEST}/"
RESOLVED = RESOLVE_ITEM + "?resolved=1"
GATE_ITEM = f"/admin/reports/{CAMPAIGN}/ministries/follow-up/{GATE_REQUEST}/"
GATED = GATE_ITEM + "?gated=1"
# A resolved join that takes the roster tick (#528), before and after it.
TICK_REQUEST = UUID(int=97)
TICK_ITEM = f"/admin/reports/{CAMPAIGN}/ministries/follow-up/{TICK_REQUEST}/"
TICKED = TICK_ITEM + "?ticked=1"
# The fixture server's answers to a Save (status, Location, body).
POSTS = {
    UPDATE: (303, SAVED, ""),
    RESOLVE_ITEM + "update": (303, RESOLVED, ""),
    GATE_ITEM + "update": (303, GATED, ""),
    TICK_ITEM + "roster": (303, TICKED, ""),
}


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
    campaign, request = CAMPAIGN, REQUEST
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
        future_message=REFUSALS["contact_future"][0],
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
        contact_zone="",
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
        contact_zone="America/Los_Angeles",
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
    saved = row | dict(
        version=4, state="in_progress", state_label=STATES["in_progress"]
    )
    # The refusals, as the view describes them.
    kind = _refusal_error(
        FollowupRefusal("outcome_kind", outcome="leave_confirmed", action="join"), {}
    )
    future = _refusal_error(FollowupRefusal("contact_future"), {})
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
            errors=[kind],
            field_error=kind,
        ),
        # A contact attempt in the future, refused with both its date and
        # time marked in error (#592).
        "/followup-item-future": values
        | dict(
            item=row,
            form=form
            | dict(
                state="in_progress",
                contact_channel="phone",
                contact_date="2099-01-01",
                contact_time="10:00",
            ),
            errors=[future],
            field_error=future,
        ),
        # The same refusal for a time this browser's clock says is past:
        # the case the server's check exists for (a wrong computer clock).
        "/followup-item-future-past": values
        | dict(
            item=row,
            form=form
            | dict(
                state="in_progress",
                contact_channel="phone",
                contact_date="2026-09-19",
                contact_time="10:00",
            ),
            errors=[future],
            field_error=future,
        ),
        "/followup-closed": values
        | dict(item=closed, rows=[closed], history=[revision]),
        "/followup-item-gated": values | dict(item=row, form=form, mutable=False),
        # The request page at its real address, before and after a save that
        # moved it to In progress and added an edit to its history.
        ITEM: values | dict(item=row, form=form, history=[revision], next_history=2),
        NEWER: values | dict(item=row, form=form, history=[revision], next_history=2),
        # The history's last page: an older edit, and only Newer history.
        OLDER: values
        | dict(
            item=row,
            form=form,
            history=[SimpleNamespace(**vars(revision) | dict(notes="Older <edit>"))],
            previous_history=1,
        ),
        # A request whose save resolves it, and one whose save comes back
        # while other campaign work makes follow-up edits unavailable.
        RESOLVE_ITEM: values
        | dict(item=row | dict(id=str(RESOLVE_REQUEST)), form=form, history=[]),
        RESOLVED: values
        | dict(item=closed | dict(id=str(RESOLVE_REQUEST)), history=[revision]),
        GATE_ITEM: values
        | dict(item=row | dict(id=str(GATE_REQUEST)), form=form, history=[]),
        GATED: values
        | dict(
            item=row | dict(id=str(GATE_REQUEST)),
            form=form,
            history=[revision],
            mutable=False,
        ),
        TICK_ITEM: values
        | dict(
            item=closed
            | dict(id=str(TICK_REQUEST), roster_eligible=True, roster_sequence=0),
            can_tick=True,
            roster_key=UUID(int=98),
            history=[],
        ),
        TICKED: values
        | dict(
            item=closed
            | dict(
                id=str(TICK_REQUEST),
                roster_eligible=True,
                roster_entered=True,
                roster_sequence=1,
                roster_by="staff@example.org",
                roster_at=moment,
            ),
            can_tick=True,
            roster_key=UUID(int=99),
            history=[],
        ),
        SAVED: values
        | dict(
            item=saved,
            form=form | dict(expected_version="4", state="in_progress"),
            history=[
                SimpleNamespace(
                    **vars(revision)
                    | dict(
                        state_label=STATES["in_progress"],
                        assignee_label="",
                        notes="Saved <note>",
                    )
                ),
                revision,
            ],
            next_history=2,
        ),
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
