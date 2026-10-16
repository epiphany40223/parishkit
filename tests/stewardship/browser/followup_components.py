"""Ministry follow-up reuses the shared browser server and production templates.

The request page is also served at its real address (``ITEM``), with a
"saved" view (``SAVED``), and the fixture server answers its Save POST with
a real Post/Redirect/Get redirect (``POSTS``). The in-place mechanism (#519)
follows a save only back to the same path, and Playwright cannot fulfil a
redirect from a route on every engine. Two more requests answer a save with
the request closed (``RESOLVED``: no form comes back) and with follow-up
edits unavailable (``GATED``: Save comes back disabled). The history's
second page (``OLDER``) and first page by number (``NEWER``) serve the
in-place history pager tests (#519 PR 6). Save and next (#534) is served
by a request whose save redirects to the next request (``ADVANCE_ITEM`` to
``NEXT_ITEM``) and one whose save, the last in the list, redirects to the
remembered queue view (``LAST_ITEM`` to ``QUEUE_VIEW``). The bulk
outcome queue (#1131, ``BULK_QUEUE``) has open join and leave requests and a
closed one (``BULK_CLOSED_QUEUE`` lists the closed one alone, #1332); its
dialog redraws the table from the remembered view ``BULK_VIEW``, which
shows the join request resolved. The tests answer the
dialog's POST (``BULK_POST``) themselves, with a route. ``GONE_QUEUE`` is
Staff's Status choice for Members no longer in ParishSoft (#1217), and
``GONE_ITEM`` one of its request pages.

Ministry requests (#1133) is this queue with its totals, By Ministry counts
and Follow-up packet, served at its real address (``REQUESTS``) so a By
Ministry count, a GET to that same address with its link filters
(``COUNTED``), is followed in place. ``LEADER`` is a leader of one Ministry,
``SORTED`` and ``SEARCHED`` the answers to an in-place re-sort and Apply.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports.chart_specs import ministry_followup
from parishkit.stewardship.reports.ministries import outcome_label
from parishkit.stewardship.reports.ministry_followup import (
    CHANNELS,
    FILTER_OUTCOMES,
    HISTORY_STATES,
    MEMBER_STATUS_MARKS,
    NOT_IN_SOURCE,
    SORTING,
    STATES,
    FollowupQuery,
)
from parishkit.stewardship.reports.ministry_followup_views import (
    BULK_KIND_HINTS,
    REFUSALS,
    _refusal_error,
    bulk_actions,
    count_links,
)
from parishkit.stewardship.web.tables import report_table
from parishkit.stewardship.workflows.followup import FollowupRefusal, outcomes_for
from parishkit.stewardship.workflows.models import STAFF_STATES

CAMPAIGN, REQUEST = UUID(int=92), UUID(int=93)
ITEM = reverse("admin:ministry_followup_item", args=[REQUEST])
UPDATE = reverse("admin:ministry_followup_update", args=[REQUEST])
SAVED = ITEM + "?saved=1"
OLDER, NEWER = ITEM + "?page=2", ITEM + "?page=1"
RESOLVE_REQUEST, GATE_REQUEST = UUID(int=94), UUID(int=96)
RESOLVE_ITEM = reverse("admin:ministry_followup_item", args=[RESOLVE_REQUEST])
RESOLVE_UPDATE = reverse("admin:ministry_followup_update", args=[RESOLVE_REQUEST])
RESOLVED = RESOLVE_ITEM + "?resolved=1"
GATE_ITEM = reverse("admin:ministry_followup_item", args=[GATE_REQUEST])
GATE_UPDATE = reverse("admin:ministry_followup_update", args=[GATE_REQUEST])
GATED = GATE_ITEM + "?gated=1"
# Save and next (#534): the remembered queue view's token, a request whose
# save moves to the next one, and the last request, whose save returns to the
# remembered queue view.
TOKEN = "ab" * 16
ADVANCE_REQUEST, NEXT_REQUEST, LAST_REQUEST = UUID(int=97), UUID(int=98), UUID(int=99)
ADVANCE_ITEM = reverse("admin:ministry_followup_item", args=[ADVANCE_REQUEST])
ADVANCE_UPDATE = reverse("admin:ministry_followup_update", args=[ADVANCE_REQUEST])
NEXT_ITEM = (
    reverse("admin:ministry_followup_item", args=[NEXT_REQUEST]) + f"?queue={TOKEN}"
)
LAST_ITEM = reverse("admin:ministry_followup_item", args=[LAST_REQUEST])
LAST_UPDATE = reverse("admin:ministry_followup_update", args=[LAST_REQUEST])
QUEUE_VIEW = reverse("admin:ministry_report") + f"?queue={TOKEN}"
# A resolved join that takes the roster tick (#528), before and after it.
TICK_REQUEST = UUID(int=101)
TICK_ITEM = reverse("admin:ministry_followup_item", args=[TICK_REQUEST])
TICK_POST = reverse("admin:ministry_followup_roster", args=[TICK_REQUEST])
TICKED = TICK_ITEM + "?ticked=1"
# The bulk outcome queue (#1131): its address, the remembered view the dialog
# redraws it from, and where the dialog posts.
BULK_QUEUE = "/followup-bulk"
# A queue page whose only request is closed: rows, none selectable (#1332).
BULK_CLOSED_QUEUE = "/followup-bulk-closed"
BULK_TOKEN = "cd" * 16
BULK_VIEW = reverse("admin:ministry_report") + f"?queue={BULK_TOKEN}"
BULK_POST = reverse("admin:ministry_followup_bulk")
BULK_KEY = UUID(int=104)
BULK_JOIN, BULK_LEAVE, BULK_CLOSED = UUID(int=105), UUID(int=106), UUID(int=107)
# Staff's view of requests for Members no longer in ParishSoft (#1217).
GONE_QUEUE, GONE_ITEM = "/followup-gone", "/followup-gone-item"
# Ministry requests (#1133): the page at its real address, the answer to its
# "51 join requests" count, and a leader of one Ministry.
REQUESTS = reverse("admin:ministry_report")
COUNTED = REQUESTS + "?ministry=9&action=join&state=any"
LEADER, SORTED, SEARCHED, NOTHING = (
    "/ministry-requests-leader",
    "/ministry-requests-sorted",
    "/ministry-requests-searched",
    # A search that matches no request: every total is zero.
    "/ministry-requests-nothing",
)
GATED_REQUESTS = "/ministry-requests-gated"
# A packet limit of one Ministry, so ticking both choices passes it (#1332).
PACKET_LIMIT = "/ministry-requests-packet-limit"
# The fixture server's answers to a Save (status, Location, body).
POSTS = {
    UPDATE: (303, SAVED, ""),
    RESOLVE_UPDATE: (303, RESOLVED, ""),
    GATE_UPDATE: (303, GATED, ""),
    ADVANCE_UPDATE: (303, NEXT_ITEM, ""),
    LAST_UPDATE: (303, QUEUE_VIEW, ""),
    TICK_POST: (303, TICKED, ""),
}


# A long Ministry name with the digit prefix some parishes use, which is
# plain text (#971); it wraps onto two lines in the queue (#1136).
LONG_MINISTRY = "105-Religious Education Catechists and Aides"


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
        action=reverse("admin:ministry_report"),
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
        member_duid=12345,
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
    # By Ministry (#1133): every Ministry in scope, with Staff's count links
    # and marks; one no longer in ParishSoft's data, one removed from the
    # campaign with nothing unresolved.
    staff = Principal(UUID(int=97), frozenset({"staff"}))
    by_ministry = [
        dict(
            duid=9,
            name="Example <Ministry>",
            active=None,
            in_campaign=True,
            joining=51,
            leaving=0,
            unresolved=40,
        ),
        dict(
            duid=12,
            name="Another Ministry",
            active=True,
            in_campaign=False,
            joining=2,
            leaving=1,
            unresolved=0,
        ),
    ]
    for summary in by_ministry:
        summary["count_links"] = count_links(staff, summary)
    totals = dict(joining=40, leaving=11, requests=62, unresolved=51, completed=11)
    values = dict(
        # Staff or an Administrator: the help explains the Removed badge
        # (#1135).
        campaign_marks=True,
        # Staff's Status choice for Members no longer in ParishSoft (#1217).
        not_in_source=NOT_IN_SOURCE,
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
        resolved_outcomes=[
            (key, outcome_label(key, "join")) for key in outcomes_for("join")
        ],
        outcomes=FILTER_OUTCOMES,
        channels=CHANNELS,
        bulk_actions=bulk_actions(),
        bulk_kind_hints=BULK_KIND_HINTS,
        bulk_key=BULK_KEY,
        refresh_url=reverse("admin:ministry_report"),
        future_message=REFUSALS["contact_future"][0],
        total=51,
        rows=[row],
        ministries=[dict(duid=9, name="Example <Ministry>")],
        metadata=dict(name="Sample campaign", source_as_of=moment, timezone="UTC"),
        # Export these requests (#1157): the applied view and zone choices.
        export_fields=query.form_values(),
        export_timezones=["America/Detroit", "UTC"],
        export_pdf_limit=1000,
        # Ministry requests' overview (#1133): the totals, By Ministry and
        # the packet's choices, every Ministry in scope.
        totals=totals,
        followup_chart=ministry_followup(totals),
        by_ministry=by_ministry,
        show_marks=True,
        packet_choices=[
            dict(duid=12, name="Another Ministry"),
            dict(duid=9, name="Example <Ministry>"),
        ],
        packet_limit=200,
        packet_key=UUID(int=109),
        # The Member's contact details on a request page (#1157), as
        # request_contacts returns them for a join: a leader's view, with
        # the unpublished phone withheld.
        contact=dict(
            kind="join",
            proposed=False,
            email_visibility="available",
            emails=["member<one>@example.org"],
            phone_visibility="not_published",
            phones={},
            address_lines=["1 Example <Street>", "Exampleton, IN 46000"],
            gender="Unspecified",
            age=66,
            report_date="2026-09-19",
        ),
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
    leave = row | dict(
        action="leave", member_name="Leaving <Member>", cannot_serve=True
    )
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
    # A Member added on the form has no DUID yet (#932), here in a Ministry
    # with a name too long for its column, since removed from the campaign.
    proposed = row | dict(
        id=str(UUID(int=100)),
        member_duid=None,
        member_name="Proposed <Member>",
        ministry_duid=11,
        ministry_name="Hospitality and welcome ministry for the " * 3 + "<end>",
        in_campaign=False,
    )
    closed = row | dict(
        open=False,
        state="resolved",
        state_label=STATES["resolved"],
        outcome="joined",
        outcome_label=outcome_label("joined", "join"),
    )
    saved = row | dict(
        version=4, state="in_progress", state_label=STATES["in_progress"]
    )
    # A full page of 25 requests with typical data (#1064): joins and leaves,
    # a leave from a Member who cannot participate, every status with its
    # outcome (the longest is "Declined / no longer interested"), a Member
    # added on the form, a Ministry since removed from the campaign, long
    # names (Ministry names as long as the longest in use, one with a digit
    # prefix, #1136), and requests with no contact recorded.
    states = [
        ("new", None),
        ("in_progress", None),
        ("resolved", "joined"),
        ("resolved", "declined"),
        ("closed_no_response", "no_response"),
        ("resolved", "leave_confirmed"),
        ("resolved", "other"),
    ]
    full_page = []
    for number in range(25):
        state, outcome = states[number % len(states)]
        full_page.append(
            row
            | dict(
                id=str(UUID(int=200 + number)),
                member_name=f"Member{number} Bartholomew-Fitzgerald <Example>",
                member_duid=None if number == 4 else 100000 + number,
                ministry_name=(
                    "Choir"
                    if number % 2 == 0
                    else LONG_MINISTRY
                    if number % 4 == 1
                    else "Hospitality and welcome ministry for the parish"
                ),
                ministry_duid=1000 + number,
                in_campaign=number != 6,
                action="leave" if number % 3 == 0 else "join",
                cannot_serve=number % 3 == 0 and number % 2 == 0,
                state=state,
                state_label=STATES[state],
                outcome=outcome,
                outcome_label=outcome_label(
                    outcome, "leave" if number % 3 == 0 else "join"
                ),
                last_contact_at=None if number % 4 == 0 else moment,
            )
        )
    # The refusals, as the view describes them.
    kind = _refusal_error(
        FollowupRefusal("outcome_kind", outcome="leave_confirmed", action="join"), {}
    )
    future = _refusal_error(FollowupRefusal("contact_future"), {})
    # The bulk outcome queue: an open join, an open leave and a closed join;
    # and the same view once the join is resolved as Joined.
    bulk_rows = [
        row | dict(id=str(BULK_JOIN), member_name="Joining <Member>"),
        leave | dict(id=str(BULK_LEAVE), version=2, cannot_serve=False),
        closed | dict(id=str(BULK_CLOSED), member_name="Closed <Member>"),
    ]
    bulk_after = [
        bulk_rows[0]
        | dict(
            open=False,
            version=4,
            state="resolved",
            state_label=STATES["resolved"],
            outcome="joined",
            outcome_label=outcome_label("joined", "join"),
        ),
        *bulk_rows[1:],
    ]
    # Two open requests whose Members ParishSoft no longer lists as current:
    # one inactive there, one deceased, with a name long enough to be cut
    # short. (A Member the data no longer has has no name; its badge alone
    # is the link, #1216.)
    gone_query = FollowupQuery(state=NOT_IN_SOURCE)
    gone_rows = [
        row
        | dict(
            id=str(UUID(int=110)),
            member_status="inactive",
            source_mark=MEMBER_STATUS_MARKS["inactive"],
        ),
        leave
        | dict(
            id=str(UUID(int=111)),
            member_name="Bartholomew-Fitzgerald, Maximilian <Example>",
            member_status="deceased",
            source_mark=MEMBER_STATUS_MARKS["deceased"],
        ),
    ]
    bulk_page = values | dict(
        query=unfiltered, queue_token=BULK_TOKEN, refresh_url=BULK_VIEW
    )
    counted = FollowupQuery(ministry="9", action="join", state="any")
    sorted_query = FollowupQuery(sort="ministry")
    joined = dict(joining=51, leaving=0, requests=51, unresolved=40, completed=11)
    searched = dict(joining=0, leaving=9, requests=60, unresolved=9, completed=51)
    nothing_query = FollowupQuery(search="No such Member")
    nothing = dict.fromkeys(
        ("joining", "leaving", "requests", "unresolved", "completed"), 0
    )
    requests_page = values | dict(
        query=unfiltered,
        export_fields=unfiltered.form_values(),
        rows=[row, proposed],
        table=_table([row, proposed], unfiltered, 51, campaign),
    )
    pages = {
        REQUESTS: requests_page,
        # The "51 join requests" count's answer: that Ministry's join
        # requests of any status, with their totals.
        COUNTED: requests_page
        | dict(
            query=counted,
            export_fields=counted.form_values(),
            rows=[row],
            table=_table([row], counted, 51, campaign),
            totals=joined,
            followup_chart=ministry_followup(joined),
        ),
        SORTED: requests_page
        | dict(
            query=sorted_query,
            export_fields=sorted_query.form_values(),
            rows=[proposed, row],
            table=_table([proposed, row], sorted_query, 51, campaign),
        ),
        SEARCHED: requests_page
        | dict(totals=searched, followup_chart=ministry_followup(searched)),
        NOTHING: requests_page
        | dict(
            query=nothing_query,
            export_fields=nothing_query.form_values(),
            rows=[],
            total=0,
            table=_table([], nothing_query, 0, campaign),
            totals=nothing,
            followup_chart=ministry_followup(nothing),
        ),
        # A leader of one Ministry: one line, no marks or campaign help.
        LEADER: requests_page
        | dict(
            by_ministry=by_ministry[:1],
            packet_choices=[dict(duid=9, name="Example <Ministry>")],
            show_marks=False,
            campaign_marks=False,
            not_in_source=None,
        ),
        GATED_REQUESTS: requests_page | dict(mutable=False),
        PACKET_LIMIT: requests_page | dict(packet_limit=1),
        BULK_QUEUE: bulk_page
        | dict(rows=bulk_rows, table=_table(bulk_rows, unfiltered, 3, campaign)),
        BULK_CLOSED_QUEUE: bulk_page
        | dict(
            rows=bulk_rows[2:], table=_table(bulk_rows[2:], unfiltered, 1, campaign)
        ),
        BULK_VIEW: bulk_page
        | dict(
            rows=bulk_after,
            table=_table(bulk_after, unfiltered, 3, campaign),
            bulk_key=UUID(int=108),
        ),
        "/followup-queue": values,
        "/followup-all": values
        | dict(
            query=unfiltered,
            export_fields=unfiltered.form_values(),
            rows=[row, proposed],
            table=_table([row, proposed], unfiltered, 51, campaign),
        ),
        "/followup-page": values
        | dict(
            query=unfiltered,
            export_fields=unfiltered.form_values(),
            rows=full_page,
            table=_table(full_page, unfiltered, 60, campaign),
        ),
        # What a Ministry leader is served: no campaign help (#1135).
        "/followup-leader": values | dict(campaign_marks=False, not_in_source=None),
        GONE_QUEUE: values
        | dict(
            query=gone_query,
            gone_view=True,
            rows=gone_rows,
            table=_table(gone_rows, gone_query, 2, campaign),
            total=2,
        ),
        GONE_ITEM: values
        | dict(item=gone_rows[0], form=form, history=[revision], gone_view=False),
        "/followup-empty": values
        | dict(rows=[], total=0, table=_table([], query, 0, campaign)),
        "/followup-gated": values | dict(mutable=False),
        # More requests than one PDF may hold (#1157): PDF is unavailable.
        "/followup-over": values | dict(total=1001),
        # A request with no saved follow-up yet (#227 empty state).
        "/followup-item-new": values | dict(item=row, form=form, history=[]),
        # A history page past its end, as a bookmarked ?page= can ask for.
        "/followup-item-past-end": values
        | dict(item=row, form=form, history=[], previous_history=1),
        "/followup-item": values
        | dict(item=row, form=form, history=[revision], next_history=2),
        "/followup-item-leave": values
        | dict(
            item=leave,
            form=form,
            contact=dict(
                kind="leave",
                proposed=False,
                email_visibility="not_published",
                emails=[],
                phone_visibility="available",
                phones={"mobile": "2025550123"},
                current_role="Chairperson",
            ),
            resolved_outcomes=[
                (key, outcome_label(key, "leave")) for key in outcomes_for("leave")
            ],
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
        # A resolved join's roster tick, before and after it is set (#528).
        TICK_ITEM: values
        | dict(
            item=closed
            | dict(id=str(TICK_REQUEST), roster_eligible=True, roster_sequence=0),
            can_tick=True,
            roster_key=UUID(int=102),
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
            roster_key=UUID(int=103),
            history=[],
        ),
        # Save and next: the next request follows; the last has none, so
        # Save and next returns to the remembered queue view.
        ADVANCE_ITEM: values
        | dict(
            item=row | dict(id=str(ADVANCE_REQUEST), member_name="First <Member>"),
            form=form,
            queue_token=TOKEN,
            next_id=str(NEXT_REQUEST),
        ),
        NEXT_ITEM: values
        | dict(
            item=row | dict(id=str(NEXT_REQUEST), member_name="Second <Member>"),
            form=form | dict(expected_version="3"),
            queue_token=TOKEN,
            next_id=str(LAST_REQUEST),
        ),
        LAST_ITEM: values
        | dict(
            item=row | dict(id=str(LAST_REQUEST), member_name="Last <Member>"),
            form=form,
            queue_token=TOKEN,
        ),
        QUEUE_VIEW: values | dict(queue_token=TOKEN),
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
