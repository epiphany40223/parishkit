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
remembered worklist view (``LAST_ITEM`` to ``QUEUE_VIEW``). ``GONE_QUEUE``
is Staff's Members choice for Members no longer in ParishSoft (#1217), and
``GONE_ITEM`` one of its request pages.

Ministry requests (#1133) is the Ministry stream's review worklist (#1684),
with By Ministry counts and the Follow-up packet below it, served at its
real address (``REQUESTS``) so a By Ministry count, a GET to that same
address with its link filters (``COUNTED``), is followed in place. Its pages
are drawn by the real ``ministry_worklist`` from detached rows
(``_worklist``). ``LEADER`` is a leader of one Ministry, ``SORTED`` and
``SEARCHED`` the answers to an in-place re-sort and Apply, ``DECIDED`` the
page after an Accept, and ``STAGES`` a page of requests at every stage.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.reports.ministries import outcome_label
from parishkit.stewardship.reports.ministry_export_views import PACKET_MESSAGE
from parishkit.stewardship.reports.ministry_followup import (
    CHANNELS,
    HISTORY_STATES,
    MEMBER_STATUS_MARKS,
    STATES,
)
from parishkit.stewardship.reports.ministry_followup_views import (
    REFUSALS,
    _refusal_error,
    count_links,
    packet_context,
)
from parishkit.stewardship.reports.ministry_worklist import (
    WorklistQuery,
    place,
    worklist_context,
)
from parishkit.stewardship.web.filter_errors import FilterError
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
# The worklist after an Accept (#1684), which the tests answer the page's own
# POST with, and a page of requests at every stage, for Staff and a leader.
DECIDED, STAGES, LEADER_STAGES = (
    "/ministry-requests-decided",
    "/ministry-requests-stages",
    "/ministry-requests-stages-leader",
)
DECIDED_NOTICE = "Example <Member> — Example <Ministry> accepted."
# Staff's view of requests for Members no longer in ParishSoft (#1217).
GONE_QUEUE, GONE_ITEM = "/followup-gone", "/followup-gone-item"
# Ministry requests (#1133): the page at its real address, the answer to its
# "51 join requests" count, and a leader of one Ministry.
REQUESTS = reverse("admin:ministry_report")
COUNTED = REQUESTS + "?ministry=9&kind=join&stage=all"
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
# A packet the server refused for ticking too many (#1332), under the same
# limit of one: both choices ticked again (and a value no box offers, never
# echoed), every box marked, the message in the hint row.
PACKET_REFUSED = "/ministry-requests-packet-refused"
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


STAFF = Principal(UUID(int=97), frozenset({"staff"}))
LEADS = Principal(UUID(int=98), frozenset({"ministry_leader"}), frozenset({9}))


def _worklist(rows, *, query=None, principal=STAFF, mutable=True, marks=None):
    """The worklist page's context for detached selection ``rows`` (#1684).

    The real ``ministry_worklist`` places and decorates them, with no last
    decision and no Family asked again (no database); ``marks`` are the
    roster ticks, by request id.
    """
    query = query or WorklistQuery()
    shaped = []
    for row in rows:
        shaped.append(
            {
                "member_label": row["member_name"] or "Not in ParishSoft",
                "source_mark": None,
                "member_status": None,
                "in_campaign": True,
                "cannot_serve": False,
                "reviewed_ministry_duid": None,
                "reviewed_ministry_name": None,
                "parish_added": False,
                "added_in_place_of_id": None,
            }
            | row
        )
    result = {
        "rows": shaped,
        "ministries": [
            dict(duid=9, name="Example <Ministry>", in_campaign=True),
            dict(duid=12, name="Another Ministry", in_campaign=True),
        ],
        "metadata": dict(name="Sample campaign", timezone="UTC"),
    }
    place(result, principal, marks=marks or {})
    return worklist_context(
        CAMPAIGN,
        query,
        principal,
        result,
        token="",
        mutable=mutable,
        action=reverse("admin:ministry_report"),
        decisions={},
        asked={},
    ) | {
        "query": query,
        "mutable": mutable,
        "can_tick": "staff" in principal.roles,
        "export_fields": query.form_values(),
        "notice": None,
    }


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
        reviewed_label="",
        decision_note="",
    )
    query = WorklistQuery(search="Example", ministry="9")
    # By Ministry (#1133): every Ministry in scope, with Staff's count links
    # and marks; one no longer in ParishSoft's data, one removed from the
    # campaign with nothing unresolved.
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
        summary["count_links"] = count_links(STAFF, summary)
    values = dict(
        # Staff or an Administrator: the help explains the Removed badge
        # (#1135).
        campaign_marks=True,
        campaign_id=campaign,
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
        channels=CHANNELS,
        future_message=REFUSALS["contact_future"][0],
        metadata=dict(name="Sample campaign", source_as_of=moment, timezone="UTC"),
        # Export these requests (#1157): the applied view and zone choices.
        export_fields=query.form_values(),
        export_pdf_limit=1000,
        # Ministry requests' overview (#1133): By Ministry and the packet's
        # choices, every Ministry in scope.
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
    unfiltered = WorklistQuery()
    every = WorklistQuery(stage="all")
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
    # a leave from a Member who cannot participate, every stage, a Member
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
        kind = "leave" if number % 3 == 0 else "join"
        if outcome in ("joined", "leave_confirmed"):
            outcome = "joined" if kind == "join" else "leave_confirmed"
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
                action=kind,
                cannot_serve=kind == "leave" and number % 2 == 0,
                state=state,
                outcome=outcome,
                last_contact_at=None if number % 4 == 0 else moment,
            )
        )
    # The refusals, as the view describes them.
    kind = _refusal_error(
        FollowupRefusal("outcome_kind", outcome="leave_confirmed", action="join"), {}
    )
    future = _refusal_error(FollowupRefusal("contact_future"), {})
    # Requests at every stage a Staff reader meets (#1684): to review, an
    # accepted join to enter by hand and one entered, an ignored leave, an
    # edited join, one the parish added and one ParishSoft already shows.
    accepted = row | dict(
        id=str(UUID(int=120)),
        member_name="Accepted <Member>",
        state="resolved",
        outcome="joined",
    )
    staged = [
        row,
        accepted,
        accepted
        | dict(id=str(UUID(int=121)), member_name="Entered <Member>", version=2),
        leave
        | dict(
            id=str(UUID(int=122)),
            member_name="Ignored <Member>",
            state="resolved",
            outcome="declined",
            cannot_serve=False,
        ),
        accepted
        | dict(
            id=str(UUID(int=123)),
            member_name="Edited <Member>",
            reviewed_ministry_duid=12,
            reviewed_ministry_name="Another Ministry",
        ),
        accepted
        | dict(
            id=str(UUID(int=124)),
            member_name="Added <Member>",
            parish_added=True,
            ministry_duid=12,
            ministry_name="Another Ministry",
        ),
        accepted
        | dict(
            id=str(UUID(int=125)),
            member_name="Confirmed <Member>",
            source_resolved=True,
        ),
    ]
    ticked = {str(UUID(int=121)): {"entered": True, "sequence": 1}}
    # Two open requests whose Members ParishSoft no longer lists as current:
    # one inactive there, one deceased, with a name long enough to be cut
    # short. (A Member the data no longer has has no name; its badge alone
    # is the link, #1216.)
    gone_query = WorklistQuery(members="gone")
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
            cannot_serve=False,
            member_status="deceased",
            source_mark=MEMBER_STATUS_MARKS["deceased"],
        ),
    ]
    counted = WorklistQuery(ministry="9", kind="join", stage="all")
    sorted_query = WorklistQuery(sort="ministry")
    nothing_query = WorklistQuery(search="No such Member")
    requests_page = values | _worklist([row, proposed])
    pages = {
        REQUESTS: requests_page,
        # The "51 join requests" count's answer: that Ministry's join
        # requests at every stage.
        COUNTED: requests_page | _worklist([row], query=counted),
        SORTED: requests_page | _worklist([proposed, row], query=sorted_query),
        SEARCHED: requests_page | _worklist([row], query=query),
        NOTHING: requests_page | _worklist([], query=nothing_query),
        # The page after Accept on the first row (#1684), with its notice.
        DECIDED: requests_page
        | _worklist(
            [
                accepted
                | dict(id=row["id"], member_name=row["member_name"], version=4),
                proposed,
            ]
        )
        | dict(notice=DECIDED_NOTICE),
        STAGES: requests_page | _worklist(staged, query=every, marks=ticked),
        # A leader of one Ministry: one line, no marks or campaign help.
        LEADER: requests_page
        | _worklist([row], principal=LEADS)
        | dict(
            by_ministry=by_ministry[:1],
            packet_choices=[dict(duid=9, name="Example <Ministry>")],
            show_marks=False,
            campaign_marks=False,
        ),
        LEADER_STAGES: requests_page
        | _worklist(staged, query=every, principal=LEADS, marks=ticked)
        | dict(show_marks=False, campaign_marks=False),
        GATED_REQUESTS: requests_page | _worklist([row, proposed], mutable=False),
        PACKET_LIMIT: requests_page | dict(packet_limit=1),
        PACKET_REFUSED: requests_page
        | dict(packet_limit=1)
        | packet_context(
            FilterError("ministries", PACKET_MESSAGE, ("9", "12", "77")),
            requests_page["packet_choices"],
        ),
        "/followup-queue": values | _worklist([row], query=query),
        "/followup-all": values | _worklist([row, proposed], query=unfiltered),
        "/followup-page": values | _worklist(full_page, query=every),
        # What a Ministry leader is served: no campaign help (#1135).
        "/followup-leader": values
        | _worklist([row], query=query, principal=LEADS)
        | dict(campaign_marks=False),
        GONE_QUEUE: values | _worklist(gone_rows, query=gone_query),
        "/followup-empty": values | _worklist([], query=query),
        "/followup-gated": values | _worklist([row], query=query, mutable=False),
        # More requests than one PDF may hold (#1157): PDF is unavailable.
        "/followup-over": values | _worklist([row], query=query) | dict(total=1001),
        QUEUE_VIEW: values | _worklist([row], query=query) | dict(queue_token=TOKEN),
    }
    items = {
        GONE_ITEM: values | dict(item=gone_rows[0], form=form, history=[revision]),
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
        "/followup-closed": values | dict(item=closed, history=[revision]),
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
        # Save and next returns to the remembered worklist view.
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
                "stewardship/ministry-requests.html",
                context | {"admin_chrome": admin} | data,
            ),
        )
        for path, data in pages.items()
    } | {
        path: (
            "text/html",
            render_to_string(
                "stewardship/ministry-followup.html",
                context | {"admin_chrome": admin} | data,
            ),
        )
        for path, data in items.items()
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
