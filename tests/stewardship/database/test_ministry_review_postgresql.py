"""Ministry requests on the review worklist, under the real web role (#1684).

Accept, Ignore, Edit, Back to review, Entered by hand and Add go through
``workflows.review`` (the page's decision service) and migration 0131's
guards; the worklist reads them back through the follow-up selection, whose
scope now follows an edited request's Ministry. A Family that asks again for
a decided request gets no second request.
"""

import re
from uuid import uuid4

import pytest
from django.db import DatabaseError, transaction
from django.urls import reverse

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.pipeline import Action, Decision, Stage
from parishkit.stewardship.reports.ministry_worklist import (
    WorklistQuery,
    read_rows,
    worklist_context,
)
from parishkit.stewardship.web.review_worklist import (
    StaleDecision,
    WorklistDecision,
)
from parishkit.stewardship.workflows.models import (
    MinistryRequest,
    MinistryRosterEntry,
    MinistryWorkflowRevision,
)
from parishkit.stewardship.workflows.review import (
    DecisionRefusal,
    MinistryDecisions,
)

from .test_background_grants_postgresql import task_login
from .test_information_followup_postgresql import search
from .test_ministry_exports_postgresql import leader
from .test_ministry_reports_postgresql import actor, setup
from .test_ministry_responses_postgresql import respond, revisit
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for

pytestmark = pytest.mark.django_db(transaction=True)


def requests():
    """Member 3's current requests: join Food pantry (9), leave Ministry 4."""
    rows = MinistryRequest.objects.exclude(state__in=["superseded", "cancelled"])
    return rows.get(ministry_duid=9, action="join"), rows.get(
        ministry_duid=4, action="leave"
    )


def decide(harness, principal, action, *targets, note="", fields=None, key=None):
    """Record one worklist decision through the page's service as web."""
    decision = WorklistDecision(
        Action(action),
        {str(target.pk): target.version for target in targets},
        note,
        key or uuid4(),
        bulk=len(targets) > 1,
        fields=fields or {},
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        service = MinistryDecisions(
            harness.service.store,
            harness.campaign.pk,
            namer=lambda request_id: f"request {request_id}",
        )
        notice = service.decide(principal, decision)
    for target in targets:
        target.refresh_from_db()
    return notice


def withdraw_leave(harness):
    """The Family answers again asking only to join Food pantry, which
    withdraws the leave of Ministry 4; the join's same-intent successor."""
    form = revisit(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [9], "leave": []}
    respond(harness, form, answers)
    current = MinistryRequest.objects.exclude(state__in=["superseded", "cancelled"])
    assert not current.filter(ministry_duid=4).exists()
    return current.get(ministry_duid=9)


def replace_join(harness, join):
    """A later response of the Family replaces ``join``'s revision.

    The Family answers again without the join (a decided request stays as
    it is), and a later request about the same Member and Ministry is put
    on that response directly, bypassing the guards, so the accepted join
    is the worklist's ``latest`` False revision at To enter by hand.
    """
    from django.db import connection

    form = revisit(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [], "leave": [4]}
    respond(harness, form, answers)
    later = (
        MinistryRequest.objects.filter(
            submission__family_id=join.submission.family_id,
            submission__mode="live",
        )
        .order_by("-submission__family_version")
        .first()
        .submission
    )
    assert later.family_version > join.submission.family_version
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        MinistryRequest.objects.create(
            submission=later,
            entity_kind=join.entity_kind,
            entity_key=join.entity_key,
            ministry_duid=join.ministry_duid,
            action="leave",
            actor_id=later.family_id,
        )
    join.refresh_from_db()


def test_a_replaced_revision_takes_only_its_roster_tick(response_service):
    """An accepted request a later response replaced (the worklist's latest
    False) offers Entered by hand alone: a forged Back to review, Ignore or
    Edit on it is stale, names the request and records nothing (R39
    review); its roster tick still records."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, _ = requests()
    decide(harness, staff, "accept", join)
    replace_join(harness, join)
    assert (join.state, join.outcome) == ("resolved", "joined")
    before = MinistryWorkflowRevision.objects.count()
    for action, fields in (
        ("back_to_review", {}),
        ("ignore", {}),
        ("edit", {"value": ["4"]}),
    ):
        with pytest.raises(StaleDecision) as raised:
            decide(harness, staff, action, join, fields=fields)
        assert raised.value.name == f"request {join.pk}", action
    assert MinistryWorkflowRevision.objects.count() == before
    assert (join.state, join.outcome, join.version) == ("resolved", "joined", 2)
    decide(harness, staff, "entered_by_hand", join)
    assert MinistryRosterEntry.objects.get(request=join).entered is True


def test_a_bulk_mark_names_the_row_someone_marked_first(response_service):
    """A bulk Entered by hand over a request already marked is stale and
    its notice names that request (as a Member no longer in ParishSoft
    does); nothing of the batch is recorded."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, leave = requests()
    decide(harness, staff, "accept", join)
    decide(harness, staff, "accept", leave)
    decide(harness, staff, "entered_by_hand", leave)
    with pytest.raises(StaleDecision) as raised:
        decide(harness, staff, "entered_by_hand", join, leave)
    assert raised.value.name == f"request {leave.pk}"
    assert not MinistryRosterEntry.objects.filter(request=join).exists()


def view(harness, principal, **values):
    """The worklist's rows for ``principal``, every stage, by request id."""
    query = WorklistQuery.parse({"stage": "all"} | values)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        result = read_rows(harness.campaign.pk, query, principal)
    return {row["id"]: row for row in result["rows"]}


def test_accept_ignore_and_back_to_review(response_service):
    """Staff decide in one step and undo it; reopening an ignored one needs a
    note, and so does undoing Entered by hand, which keeps the Accept
    (#1840)."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, leave = requests()
    notice = decide(harness, staff, "accept", join, note="Called them")
    assert notice == f"request {join.pk} accepted."
    assert (join.state, join.outcome, join.version) == ("resolved", "joined", 2)
    revision = MinistryWorkflowRevision.objects.get(request=join, expected_version=1)
    assert revision.decision_note == "Called them"
    decide(harness, staff, "ignore", leave)
    assert (leave.state, leave.outcome) == ("resolved", "declined")
    rows = view(harness, staff)
    assert rows[str(join.pk)]["placement"].stage is Stage.TO_ENTER_BY_HAND
    assert rows[str(leave.pk)]["placement"].stage is Stage.IGNORED
    assert rows[str(leave.pk)]["decision_label"] == "Ignored: Stayed in the Ministry"
    # Reopening the ignored request needs its note.
    with pytest.raises(ValueError):
        decide(harness, staff, "back_to_review", leave)
    decide(harness, staff, "back_to_review", leave, note="Asked again by phone")
    assert (leave.state, leave.outcome) == ("in_progress", None)
    assert leave.resolved_at is None
    # Entered by hand ticks the accepted join; Back to review undoes the
    # tick, with a note, back to To enter by hand.
    decide(harness, staff, "entered_by_hand", join)
    assert MinistryRosterEntry.objects.get(request=join).entered
    assert view(harness, staff)[str(join.pk)]["placement"].stage is (
        Stage.ENTERED_BY_HAND
    )
    with pytest.raises(ValueError, match="needs a note"):
        decide(harness, staff, "back_to_review", join)
    decide(harness, staff, "back_to_review", join, note="Not typed yet")
    assert (join.state, join.outcome) == ("resolved", "joined")
    assert view(harness, staff)[str(join.pk)]["placement"].stage is (
        Stage.TO_ENTER_BY_HAND
    )
    # A decision on a version someone else moved is stale; nothing is written.
    stale = MinistryRequest.objects.get(pk=leave.pk)
    stale.version = 1
    with pytest.raises(StaleDecision):
        decide(harness, staff, "accept", stale)
    assert MinistryWorkflowRevision.objects.count() == 4


def test_bulk_decisions_are_all_or_none(response_service):
    """A bulk Accept records one revision a request and one bulk event; a
    stale row refuses the whole batch."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, leave = requests()
    stale = MinistryRequest.objects.get(pk=leave.pk)
    stale.version = 7
    with pytest.raises(StaleDecision):
        decide(harness, staff, "accept", join, stale)
    assert MinistryWorkflowRevision.objects.count() == 0
    key = uuid4()
    assert decide(harness, staff, "accept", join, leave, key=key) == (
        "2 requests accepted."
    )
    assert (join.outcome, leave.outcome) == ("joined", "leave_confirmed")
    assert (
        AuditEvent.objects.filter(event_type="ministry_followup_bulk_applied").count()
        == 1
    )
    # The same post again replays: nothing more is written.
    join.version, leave.version = 1, 1
    decide(harness, staff, "accept", join, leave, key=key)
    assert MinistryWorkflowRevision.objects.count() == 2


def test_edit_moves_the_request_into_another_leaders_scope(response_service):
    """Staff edit a join into Ministry 4: its leaders now see and decide it;
    the Food pantry leader still sees it, read only."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    pantry = actor(harness, {"ministry_leader"}, (9,))
    fours = actor(harness, {"ministry_leader"}, (4,))
    join, _leave = requests()
    with pytest.raises(ValueError):
        decide(harness, staff, "edit", join, fields={"value": ["9"]})
    with pytest.raises(ValueError):
        decide(harness, staff, "edit", join, fields={"value": ["77"]})
    # Member 3 still has a current request about Ministry 4 (the leave), so
    # a second one carrying it forward is refused, in Python and in SQL.
    with pytest.raises(DecisionRefusal) as refused:
        decide(harness, staff, "edit", join, fields={"value": ["4"]})
    assert refused.value.code == "edit_exists"
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="not valid for this request"),
        work_transaction(),
    ):
        MinistryWorkflowRevision.objects.create(
            request=join,
            actor_id=staff.identity,
            request_key=uuid4(),
            expected_version=join.version,
            state="resolved",
            outcome="joined",
            reviewed_ministry_duid=4,
        )
    join = withdraw_leave(harness)
    decide(harness, staff, "edit", join, fields={"value": ["4"]})
    assert (join.state, join.outcome, join.reviewed_ministry_duid) == (
        "resolved",
        "joined",
        4,
    )
    edited = view(harness, fours)[str(join.pk)]
    assert edited["placement"].decision is Decision.ACCEPTED_EDITED
    assert edited["stage"] == "accepted"
    assert edited["stage_label"] == "Accepted (edited)"
    assert str(join.pk) in view(harness, pantry)
    # The edited request still holds Food pantry, so no second request
    # about it can be added (Back to review would return it there).
    added = {
        "value_member": ["3"],
        "value_ministry": ["9"],
        "value_action": ["join"],
        "value_in_place_of": [""],
    }
    with pytest.raises(DecisionRefusal) as refused:
        decide(harness, staff, "add", fields=added)
    assert refused.value.code == "add_exists"
    # The Food pantry leader may no longer decide it; Ministry 4's may.
    with pytest.raises(PermissionError):
        decide(harness, pantry, "back_to_review", join)
    key = uuid4()
    decide(harness, fours, "back_to_review", join, key=key)
    assert (join.state, join.reviewed_ministry_duid) == ("in_progress", None)
    assert str(join.pk) not in view(harness, fours)
    # Back with Food pantry, the request is outside the Ministry 4 leader's
    # scope; their retry of the same post is a replay, not a denial, and a
    # second, outdated post from them is stale.
    count = MinistryWorkflowRevision.objects.count()
    join.version -= 1
    decide(harness, fours, "back_to_review", join, key=key)
    join.version -= 1
    with pytest.raises(StaleDecision):
        decide(harness, fours, "accept", join)
    # On the request's current version, it is simply not theirs any more.
    join.refresh_from_db()
    with pytest.raises(PermissionError):
        decide(harness, fours, "accept", join)
    assert MinistryWorkflowRevision.objects.count() == count


def test_a_leader_edits_only_into_a_ministry_they_lead(response_service):
    """A leader of Food pantry alone cannot move a request to Ministry 4; one
    who leads both can, and the SQL guard refuses the same shortcut."""
    harness = setup(response_service)
    pantry = actor(harness, {"ministry_leader"}, (9,))
    both = actor(harness, {"ministry_leader"}, (4, 9))
    join, _leave = requests()
    with pytest.raises(PermissionError):
        decide(harness, pantry, "edit", join, fields={"value": ["4"]})
    assert MinistryWorkflowRevision.objects.count() == 0
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="not valid for this request"),
        work_transaction(),
    ):
        MinistryWorkflowRevision.objects.create(
            request=join,
            actor_id=pantry.identity,
            request_key=uuid4(),
            expected_version=join.version,
            state="resolved",
            outcome="joined",
            reviewed_ministry_duid=4,
        )
    join = withdraw_leave(harness)
    decide(harness, both, "edit", join, fields={"value": ["4"]})
    assert join.reviewed_ministry_duid == 4
    # The roster rows list the Ministry to enter: the edited one.
    from parishkit.stewardship.reports.ministry_roster_views import roster_rows

    staff = actor(harness, {"staff"}, ())
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        rows, _ = roster_rows(harness.campaign.pk, staff, "all")
    (row,) = [found for found in rows if found["id"] == str(join.pk)]
    assert (row["ministry_duid"], row["ministry_name"]) == (4, "Choir")


def test_replays_and_a_family_answer_after_back_to_review(response_service):
    """Posting the same Back to review again replays it, as does withdrawing
    an added request twice; a request sent back to review is open again, so
    the Family's next answer carries it forward as before."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, leave = requests()
    decide(harness, staff, "accept", join)
    key = uuid4()
    decide(harness, staff, "back_to_review", join, key=key)
    assert join.state == "in_progress"
    count = MinistryWorkflowRevision.objects.count()
    join.version -= 1
    decide(harness, staff, "back_to_review", join, key=key)
    assert MinistryWorkflowRevision.objects.count() == count
    successor = withdraw_leave(harness)
    assert successor.pk != join.pk and successor.state == "in_progress"
    # A leader never inserts a parish-added request, even straight in SQL.
    pantry = actor(harness, {"ministry_leader"}, (9,))
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError, match="parish adds needs Staff"),
        work_transaction(),
    ):
        MinistryRequest.objects.create(
            submission=successor.submission,
            entity_kind="member",
            entity_key="3",
            ministry_duid=4,
            action="join",
            state="new",
            actor_id=pantry.identity,
            parish_added=True,
        )


def test_the_sql_guard_refuses_unpaired_reopens(response_service):
    """Directly, the revision guard refuses a reopen with no note of an
    ignored request and a withdrawal of a Family's request."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, _leave = requests()
    decide(harness, staff, "ignore", join)
    base = dict(
        request=join,
        actor_id=staff.identity,
        request_key=uuid4(),
        expected_version=join.version,
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for values in (
            dict(state="in_progress", decision_note=""),
            dict(state="cancelled", decision_note="Withdraw it"),
        ):
            with (
                pytest.raises(DatabaseError, match="not valid for this request"),
                work_transaction(),
            ):
                MinistryWorkflowRevision.objects.create(**(base | values))


def test_staff_add_a_request_in_place_of_an_ignored_one(response_service):
    """Add puts an accepted request on the Family's latest response, named in
    place of the ignored one; a replay writes nothing more, a second Add is
    refused, and Back to review withdraws it."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    pantry = actor(harness, {"ministry_leader"}, (9,))
    join, leave = requests()
    decide(harness, staff, "ignore", join)
    fields = {
        "value_member": ["3"],
        "value_ministry": ["4"],
        "value_action": ["join"],
        "value_in_place_of": [str(join.pk)],
    }
    # Member 3 still has a current request for Ministry 4 (the leave).
    with pytest.raises(DecisionRefusal) as refused:
        decide(harness, staff, "add", fields=fields)
    assert refused.value.code == "add_exists"
    for wrong, code in (("999999", "add_no_response"), ("abc", "add_member")):
        with pytest.raises(DecisionRefusal) as refused:
            decide(harness, staff, "add", fields=fields | {"value_member": [wrong]})
        assert refused.value.code == code
    with pytest.raises(PermissionError):
        decide(harness, pantry, "add", fields=fields)
    # The Family answers again without the leave, which withdraws it.
    form = revisit(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [], "leave": []}
    latest = respond(harness, form, answers)
    leave.refresh_from_db()
    assert leave.state == "cancelled"
    # Only an ignored request of the same Member can be replaced, never
    # one the Family withdrew.
    with pytest.raises(DecisionRefusal) as refused:
        decide(
            harness,
            staff,
            "add",
            fields=fields | {"value_in_place_of": [str(leave.pk)]},
        )
    assert refused.value.code == "add_in_place_of"
    key = uuid4()
    assert decide(harness, staff, "add", fields=fields, key=key).endswith(" added.")
    added = MinistryRequest.objects.get(parish_added=True)
    assert (added.submission_id, added.entity_key, added.ministry_duid) == (
        latest.pk,
        "3",
        4,
    )
    assert (added.state, added.outcome, added.added_in_place_of_id) == (
        "resolved",
        "joined",
        join.pk,
    )
    assert added.actor_id == staff.identity
    row = view(harness, staff)[str(added.pk)]
    assert row["placement"].stage is Stage.TO_ENTER_BY_HAND
    assert row["placement"].decision is Decision.ADDED
    # A replay writes nothing more; another Add is refused.
    count = MinistryRequest.objects.count()
    assert decide(harness, staff, "add", fields=fields, key=key).endswith(
        "was already added."
    )
    with pytest.raises(DecisionRefusal) as refused:
        decide(harness, staff, "add", fields=fields)
    assert refused.value.code == "add_exists"
    assert MinistryRequest.objects.count() == count
    # Back to review withdraws an added request; the same post replays.
    key = uuid4()
    decide(harness, staff, "back_to_review", added, key=key)
    assert added.state == "cancelled"
    assert str(added.pk) not in view(harness, staff)
    added.version -= 1
    decide(harness, staff, "back_to_review", added, key=key)
    # Withdrawn on that response, it cannot be added there again.
    with pytest.raises(DecisionRefusal) as refused:
        decide(harness, staff, "add", fields=fields)
    assert refused.value.code == "add_withdrawn"


def test_family_asked_again_creates_no_second_request(response_service):
    """A Family that ticks a decided join again gets no new request; the
    decided one keeps its decision and its row says when they asked."""
    harness = setup(response_service)
    staff = actor(harness, {"staff"}, ())
    join, _leave = requests()
    decide(harness, staff, "ignore", join)
    before = MinistryRequest.objects.count()
    form = revisit(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [9], "leave": []}
    respond(harness, form, answers)
    assert MinistryRequest.objects.filter(ministry_duid=9).count() == 1
    assert MinistryRequest.objects.count() == before
    join.refresh_from_db()
    assert (join.state, join.outcome) == ("resolved", "declined")
    query = WorklistQuery.parse({"stage": "ignored"})
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        result = read_rows(harness.campaign.pk, query, staff)
        context = worklist_context(
            harness.campaign.pk,
            query,
            staff,
            result,
            token="",
            mutable=True,
            action="/admin/reports/ministries/",
        )
    (row,) = [r for r in context["worklist"].rows if r["id"] == str(join.pk)]
    assert "Family asked again" in row["decision_label"]


def test_the_page_records_a_decision_in_place(response_service, google):
    """The worklist page posts a decision to itself and answers with the page
    redrawn, its notice saying what was recorded."""
    harness = setup(response_service)
    browser, head, _, _ = leader(harness, google)
    join, leave = requests()
    route = reverse("admin:ministry_report")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, route)
        assert response.status_code == 200
        assert b"Ministry requests to review" in body
        assert f'id="requests-row-{join.pk}"'.encode() in body
        assert str(leave.pk).encode() not in body
        assert b"Follow-up progress" not in body
        assert b'name="decide" value="add"' not in body
        key = re.search(
            rb'data-confirm-with="decide=accept&amp;version=1&amp;request_key=([^"]+)"',
            body,
        )[1].decode()
        response, body = search(
            browser,
            route,
            {
                "stage": "to_review",
                "search": "",
                "ministry": "",
                "kind": "any",
                "members": "current",
                "page": "1",
                "size": "50",
                "sort": "newest",
                "decide": "accept",
                "item": str(join.pk),
                "version": "1",
                "request_key": key,
                "note": "",
            },
        )
    assert response.status_code == 200
    assert b"accepted." in body
    join.refresh_from_db()
    assert (join.state, join.outcome) == ("resolved", "joined")
    assert MinistryWorkflowRevision.objects.get(request=join).actor_id == head
