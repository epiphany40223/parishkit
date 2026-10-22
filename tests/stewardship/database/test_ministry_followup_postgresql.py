"""Scoped Ministry follow-up history under the real web role and SQL pairing."""

import json
import re
from datetime import UTC, datetime, timedelta
from html import escape
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, connection, transaction
from django.db.models import F
from django.urls import reverse

from parishkit.stewardship.accounts.policy import Principal, current_principal
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.ministries import MinistryQuery, ministry_page
from parishkit.stewardship.reports.ministry_followup import (
    FollowupQuery,
    followup_history,
    followup_page,
    my_ministries,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.workflows.followup import (
    BulkStale,
    FollowupRefusal,
    MemberNotInSource,
    WorkflowChange,
    _system,
    _targets,
    apply_bulk_outcome,
    outcomes_for,
    update_request,
)
from parishkit.stewardship.workflows.models import (
    MinistryRequest,
    MinistryWorkflowRevision,
)

from ..census_factory import member
from ..policy_factory import address, domain
from .auth_builders import signed_in, unguarded
from .campaign_builders import change
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import post
from .test_information_followup_postgresql import search
from .test_ministry_exports_postgresql import leader
from .test_ministry_reports_postgresql import actor, setup
from .test_ministry_reports_postgresql import page as report_page
from .test_ministry_reports_postgresql import prepare as report_prepare
from .test_ministry_responses_postgresql import ministry_source, respond, revisit
from .test_policy_postgresql import user
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for
from .test_source_families_postgresql import promote

pytestmark = pytest.mark.django_db(transaction=True)


def requests():
    """The harness Family asks Member 3 to join Ministry 9 and leave Ministry 4."""
    rows = MinistryRequest.objects.exclude(state__in=["superseded", "cancelled"])
    return rows.get(ministry_duid=9), rows.get(ministry_duid=4)


def edit(harness, actor, target, *, version=None, key=None, **values):
    """Edit under the real web database role, without migration-owner grants."""
    change = WorkflowChange(
        **(dict(state="in_progress", outcome=None, notes="") | values)
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return update_request(
            harness.service.store,
            actor,
            target.pk,
            expected_version=target.version if version is None else version,
            request_key=key or uuid4(),
            change=change,
        )


def legacy_assign(harness, actor, target, assignee, notes=""):
    """Record an assignment as the removed feature did before #552.

    The installed SQL still admits one, so production can hold such requests;
    this writes the same revision, projection and audit the old edit wrote.
    """
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        work_transaction(),
    ):
        (locked,) = _targets(harness.service.store, actor, {target.pk: target.version})
        revision = MinistryWorkflowRevision.objects.create(
            request_id=locked.pk,
            actor_id=actor,
            request_key=uuid4(),
            expected_version=locked.version,
            assignee_id=assignee,
            state="assigned",
            notes=notes,
        )
        MinistryRequest.objects.filter(pk=locked.pk).update(
            state="assigned", assignee_id=assignee, version=F("version") + 1
        )
        record_action(
            Action.MINISTRY_REQUEST_UPDATED,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor,
            subject_id=revision.pk,
            parish_id=_system().active_configuration.parish.pk,
            campaign_id=locked.submission.campaign_id,
            context={
                "outcome": Outcome.CHANGED,
                "before_version": locked.version,
                "after_version": locked.version + 1,
                "ministry_duid": locked.ministry_duid,
            },
        )
    target.refresh_from_db()
    return revision


def latest_notes(request_id):
    """The newest Staff notes across the same-intent chain, or None."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        rows, _ = followup_history(request_id, 1)
    return rows[0].notes if rows else None


def test_scoped_history_replay_stale_writers_and_sql_pairing(response_service, google):
    """Leaders edit only their Ministry; SQL rejects every unpaired shortcut."""
    harness = setup(response_service)
    _, head, _, _ = leader(harness, google)
    admin = user("admin@example.org").pk
    outsider = user("outsider@example.org").pk
    join, leave = requests()
    called, key = database_now() - timedelta(hours=1), uuid4()
    values = dict(
        state="in_progress",
        notes="Left a private voicemail",
        contact_channel="phone",
        contact_at=called,
        contact_notes="No answer",
    )
    first = edit(harness, head, join, key=key, **values)
    join.refresh_from_db()
    assert (join.state, join.assignee_id, join.version) == ("in_progress", None, 2)
    assert join.outcome is None and join.resolved_at is None
    # A replay returns the same history; a rebound key or stale form does not write.
    assert edit(harness, head, join, version=1, key=key, **values).pk == first.pk
    with pytest.raises(ValueError):
        edit(harness, head, join, version=1, key=key, **(values | {"notes": "Other"}))
    with pytest.raises(StaleRecordError):
        edit(harness, admin, join, version=1, notes="Stale form")
    # Scope is per Ministry for the actor.
    with pytest.raises(PermissionError):
        edit(harness, head, leave)
    with pytest.raises(PermissionError):
        edit(harness, outsider, join)
    with pytest.raises(PermissionError):
        edit(harness, head, MinistryRequest(pk=uuid4(), version=1))
    for invalid in (
        dict(state="resolved", outcome="leave_confirmed"),
        dict(contact_channel="email", contact_at=database_now() + timedelta(days=1)),
    ):
        with pytest.raises(ValueError):
            edit(harness, admin, join, **invalid)
    closed = edit(harness, admin, leave, state="resolved", outcome="leave_confirmed")
    leave.refresh_from_db()
    assert leave.resolved_at == closed.created_at
    assert leave.resolution_source_id is None and leave.version == 2
    with pytest.raises(StaleRecordError):
        edit(harness, admin, leave, notes="Closed outcomes are immutable")
    assert MinistryWorkflowRevision.objects.count() == 2
    contexts = AuditContext.objects.filter(
        event__event_type="ministry_request_updated"
    ).order_by("event__created_at")
    assert [(row.event.subject_id, row.context) for row in contexts] == [
        (
            revision.pk,
            {
                "outcome": "changed",
                "before_version": 1,
                "after_version": 2,
                "ministry_duid": ministry,
            },
        )
        for revision, ministry in ((first, 9), (closed, 4))
    ]
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        # Direct SQL cannot rewrite history or move a request without a receipt.
        for statement in (
            "UPDATE stewardship_ministry_revision SET notes='overwritten'",
            "DELETE FROM stewardship_ministry_revision",
            "UPDATE stewardship_ministry_request SET state='in_progress',"
            f"version=version+1 WHERE id='{join.pk}'",
            f"UPDATE stewardship_ministry_request SET assignee_id='{admin}',"
            f"version=version+1 WHERE id='{join.pk}'",
        ):
            with (
                pytest.raises(DatabaseError),
                work_transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
        orphan = dict(
            request=join,
            request_key=uuid4(),
            expected_version=join.version,
            assignee_id=head,
            state="in_progress",
        )
        # No forged attribution, and no revision without its projection/audit.
        with pytest.raises(DatabaseError), work_transaction():
            MinistryWorkflowRevision.objects.create(actor_id=outsider, **orphan)
        with pytest.raises(DatabaseError), work_transaction():
            MinistryWorkflowRevision.objects.create(actor_id=head, **orphan)
        # The application rejects each of these first, so reach the guard
        # directly. The inner savepoint proves the BEFORE guard refuses them at
        # the statement, not the deferred pairing trigger at commit.
        moment = database_now()
        # Positive control: the unmodified row passes the BEFORE guard, so each
        # refusal below is caused by its one changed value and nothing else.
        with work_transaction(), transaction.atomic():
            MinistryWorkflowRevision.objects.create(actor_id=head, **orphan)
            transaction.set_rollback(True)
        invalid_for_request = "not valid for this request"
        lacks_authority = "lacks current authority/version"
        for invalid, refusal in (
            (dict(assignee_id=outsider), invalid_for_request),
            (
                dict(state="resolved", outcome="leave_confirmed", assignee_id=None),
                invalid_for_request,
            ),
            (
                dict(contact_channel="phone", contact_at=moment + timedelta(days=1)),
                invalid_for_request,
            ),
            (dict(notes="n" * 5001), lacks_authority),
            (
                dict(
                    contact_channel="phone",
                    contact_at=moment,
                    contact_notes="c" * 2001,
                ),
                lacks_authority,
            ),
        ):
            with (
                work_transaction(),
                pytest.raises(DatabaseError, match=refusal),
                transaction.atomic(),
            ):
                MinistryWorkflowRevision.objects.create(
                    actor_id=head, **(orphan | invalid)
                )
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        pytest.raises(DatabaseError),
        work_transaction(),
    ):
        MinistryWorkflowRevision.objects.create(actor_id=head, **orphan)
    assert MinistryWorkflowRevision.objects.count() == 2


def test_queue_rows_carry_each_members_duid(response_service):
    """Each row carries its Member's DUID for the Member DUID column (#932).

    The installed selection does not return the Member key, so the page
    reads it for the shown rows under the web role's own grants; a Member
    added on the form has none yet (the page shows New Member).
    """
    harness = setup(response_service)
    form = revisit(harness)
    answers = answers_for(form)
    identifier = str(uuid4())
    answers["proposed_members"][identifier] = member(
        first_name="New", last_name="Person"
    )
    answers["ministries"]["proposed_members"][identifier] = {"join": [9]}
    respond(harness, form, answers)
    staff = Principal(user("admin@example.org").pk, frozenset({"staff"}), frozenset())
    rows = read(harness, staff, ministry="9", state="any")["rows"]
    assert {row["entity_kind"]: row["member_duid"] for row in rows} == {
        "member": 3,
        "proposed_member": None,
    }


def test_queue_names_members_last_first(response_service):
    """Member names read "Last, first", so the Member sort orders by surname;
    the search still finds the "first last" form (migration 0069, #932)."""
    from parishkit.stewardship.source.snapshot_models import SourceCurrent
    from parishkit.stewardship.source.version_models import SnapshotMember

    harness = setup(response_service)
    form = revisit(harness)
    answers = answers_for(form)
    identifier = str(uuid4())
    answers["proposed_members"][identifier] = member(
        first_name="Zed", last_name="Aardvark"
    )
    answers["ministries"]["proposed_members"][identifier] = {"join": [9]}
    respond(harness, form, answers)
    snapshot = SourceCurrent.objects.values_list("snapshot_id", flat=True).get()
    three = SnapshotMember.objects.get(snapshot_id=snapshot, source_key="3")
    record = json.loads(three.payload.canonical)
    known = f"{record['lastName'].strip()}, {record['firstName'].strip()}"
    staff = Principal(user("admin@example.org").pk, frozenset({"staff"}), frozenset())
    rows = read(harness, staff, ministry="9", state="any", sort="name")["rows"]
    assert [row["member_name"] for row in rows] == sorted(
        ["Aardvark, Zed", known], key=str.lower
    )
    for text in ("Zed Aardvark", "aardvark, zed"):
        (found,) = read(harness, staff, ministry="9", state="any", search=text)["rows"]
        assert found["member_name"] == "Aardvark, Zed"


# The rule before the reviewed Family form (#1705): forms drawn before
# its release, as one open across the deploy is.
@pytest.mark.usefixtures("earlier_rule")
def test_family_resubmission_retains_staff_work(response_service):
    """Same intent inherits state and history; withdrawal ends the workflow.

    SQL still makes a successor inherit an assignment recorded before #552,
    so such a request keeps reading as New until its next edit clears it.
    """
    harness = setup(response_service)
    admin = user("admin@example.org").pk
    join, leave = requests()
    noted = legacy_assign(harness, admin, join, admin, notes="Call again")
    form = revisit(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [9], "leave": []}
    respond(harness, form, answers)
    successor = MinistryRequest.objects.get(ministry_duid=9, state="assigned")
    join.refresh_from_db()
    leave.refresh_from_db()
    assert (join.state, join.superseded_by_id) == ("superseded", successor.pk)
    assert (successor.state, successor.assignee_id) == ("assigned", admin)
    assert successor.version == 1 and leave.state == "cancelled"
    # Notes are read across the chain, never copied, and old forms are dead.
    assert latest_notes(successor.pk) == noted.notes
    assert latest_notes(leave.pk) is None
    staff = Principal(admin, frozenset({"staff"}), frozenset())
    (row,) = read(harness, staff, ministry="9")["rows"]
    assert (row["id"], row["state"], row["state_label"]) == (
        str(successor.pk),
        "new",
        "New",
    )
    assert "assignee_id" not in row
    for stale in (join, leave):
        with pytest.raises(StaleRecordError):
            edit(harness, admin, stale, notes="Bound to the replaced request")
    chained = edit(harness, admin, successor, notes="Reached the Member")
    assert latest_notes(successor.pk) == chained.notes
    successor.refresh_from_db()
    assert (successor.state, successor.assignee_id) == ("in_progress", None)
    assert MinistryWorkflowRevision.objects.filter(request=join).count() == 1


def test_legacy_assignment_reads_as_new_and_next_edit_clears_it(
    response_service, google
):
    """A request assigned before #552 shows as New everywhere and edits as New."""
    harness = setup(response_service)
    _, head, _, _ = leader(harness, google)
    admin = user("admin@example.org").pk
    join, _ = requests()
    legacy_assign(harness, admin, join, head, notes="Old hand-off")
    assert (join.state, join.assignee_id, join.version) == ("assigned", head, 2)
    staff = Principal(admin, frozenset({"staff"}), frozenset())
    (row,) = read(harness, staff, ministry="9")["rows"]
    assert (row["state"], row["state_label"], row["open"]) == ("new", "New", True)
    assert "assignee_id" not in row
    # The default Unresolved filter and Any status keep it in the queue. The
    # frozen selection filters on the stored state, so an explicit New filter
    # does not match it until its next edit (recorded on #552).
    assert read(harness, staff, ministry="9", state="any")["total"] == 1
    assert read(harness, staff, ministry="9", state="new")["total"] == 0
    # The Ministry report, its exports and packets share the same label.
    assert report_page(harness, ministry=9)["rows"][0]["state_label"] == "New"
    edit(harness, head, join, state="new", notes="Old hand-off")
    join.refresh_from_db()
    assert (join.state, join.assignee_id, join.version) == ("new", None, 3)
    assert read(harness, staff, ministry="9", state="new")["total"] == 1
    # Stored history is never rewritten: the old entry keeps its assignee.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        rows, _ = followup_history(join.pk, 1)
    assert [(item.state, item.assignee_id) for item in rows] == [
        ("new", None),
        ("assigned", head),
    ]


def test_work_gate_and_revoked_actor_close_mutation(response_service):
    """A purge gate or disabled account stops edits before any history is written."""
    harness = setup(response_service)
    admin = user("admin@example.org").pk
    join, _ = requests()
    PortalUser.objects.filter(pk=admin).update(disabled=True, version=F("version") + 1)
    with pytest.raises((PermissionError, ObjectDoesNotExist)):
        edit(harness, admin, join, notes="Disabled")
    # Re-enabling is refused in SQL (#306); only the owner can undo it.
    with unguarded():
        PortalUser.objects.filter(pk=admin).update(
            disabled=False, version=F("version") + 1
        )
    # Only the future purge owner's sentinel is synthetic in this disposable DB.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_campaign_work_gate DISABLE TRIGGER USER"
        )
        CampaignWorkGate.objects.create(
            campaign=harness.campaign,
            request_id=uuid4(),
            initiated_by_id=uuid4(),
            state="preparing",
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE stewardship_campaign_work_gate ENABLE TRIGGER USER")
    with pytest.raises(PermissionError):
        edit(harness, admin, join, notes="Gated")
    # The SQL guard independently refuses history while the gate is held.
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        pytest.raises(DatabaseError),
        work_transaction(),
    ):
        MinistryWorkflowRevision.objects.create(
            request=join,
            actor_id=admin,
            request_key=uuid4(),
            expected_version=join.version,
            state="in_progress",
        )
    assert MinistryWorkflowRevision.objects.count() == 0


def read(harness, principal, *, request_id=None, **values):
    """Query through actual restricted SQL grants, not the migration owner."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        return followup_page(
            harness.campaign.pk,
            FollowupQuery.parse(values),
            principal,
            request_id=request_id,
        )


def test_scoped_queue_detail_and_chain_history(response_service, google):
    """Leaders see only their Ministry; history and dates follow the chain."""
    harness = setup(response_service)
    _, head, _, _ = leader(harness, google)
    admin = user("admin@example.org").pk
    join, leave = requests()
    called = database_now() - timedelta(hours=2)
    edit(
        harness,
        admin,
        join,
        notes="PRIVATE-NOTE first call",
        contact_channel="phone",
        contact_at=called,
    )
    staff = Principal(admin, frozenset({"staff"}), frozenset())
    scoped = Principal(head, frozenset({"ministry_leader"}), frozenset({9}))
    nobody = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset())
    page = read(harness, staff)
    assert page["total"] == 2
    # JSON text would render as an empty instant; the page needs a real one.
    assert page["metadata"]["source_as_of"].utcoffset() is not None
    # Both requests share one submission instant, so only membership is fixed.
    assert sorted(row["ministry_duid"] for row in page["rows"]) == [4, 9]
    row = read(harness, staff, ministry="9")["rows"][0]
    assert (row["state"], row["version"]) == ("in_progress", 2)
    assert "assignee_id" not in row
    assert row["notes"] == "PRIVATE-NOTE first call"
    assert row["phone_contact_at"] == called and row["email_contact_at"] is None
    # No contact, address or financial payload is ever projected here.
    assert not {"emails", "phones", "address"} & set(row)
    # Closed filter vocabularies select on the current projection.
    assert read(harness, staff, state="in_progress", action="join")["total"] == 1
    assert read(harness, staff, action="leave", state="in_progress")["total"] == 0
    assert read(harness, staff, action="leave", state="new")["total"] == 1
    assert read(harness, staff, search="no such member")["total"] == 0
    # A leader's scope hides other Ministries from lists, options and detail.
    limited = read(harness, scoped)
    assert [item["duid"] for item in limited["ministries"]] == [9]
    assert [item["ministry_duid"] for item in limited["rows"]] == [9]
    assert read(harness, scoped, request_id=join.pk)["rows"][0]["id"] == str(join.pk)
    with pytest.raises(ObjectDoesNotExist):
        read(harness, scoped, request_id=leave.pk)
    with pytest.raises(PermissionError):
        read(harness, nobody)
    for invalid in (
        {"state": "invented"},
        {"state": "assigned"},
        {"assignee": "any"},
        {"ministry": "09"},
        {"ministry": "0"},
        {"sort": "random"},
        {"unknown": "x"},
    ):
        with pytest.raises(ValueError):
            FollowupQuery.parse(invalid)
    # A same-intent resubmission keeps the queue row's work, read via the chain.
    form = revisit(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [9], "leave": [4]}
    respond(harness, form, answers)
    successor = MinistryRequest.objects.get(ministry_duid=9, state="in_progress")
    current = read(harness, staff, ministry="9")
    assert [item["id"] for item in current["rows"]] == [str(successor.pk)]
    assert current["rows"][0]["notes"] == "PRIVATE-NOTE first call"
    assert current["rows"][0]["phone_contact_at"] == called
    # Every version (the request page's and Roster changes' read); the
    # queue itself no longer offers it (#1131), so it is not a parsed filter.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True), transaction.atomic():
        everything = followup_page(
            harness.campaign.pk,
            FollowupQuery(ministry="9", history="all", state="any"),
            staff,
        )
    assert {item["id"] for item in everything["rows"]} == {
        str(join.pk),
        str(successor.pk),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        rows, more = followup_history(successor.pk, 1)
        assert [item.notes for item in rows] == ["PRIVATE-NOTE first call"]
        assert not more and followup_history(successor.pk, 2) == ([], False)


def test_followup_scope_comes_from_the_actor_not_python(response_service, google):
    """Python claiming Staff for a leader cannot widen the queue (#389 L3)."""
    harness = setup(response_service)
    _, head, _, _ = leader(harness, google)
    join, leave = requests()
    widened = Principal(head, frozenset({"staff"}), frozenset())
    queue = read(harness, widened)
    assert [item["duid"] for item in queue["ministries"]] == [9]
    assert [item["ministry_duid"] for item in queue["rows"]] == [9]
    assert read(harness, widened, request_id=join.pk)["rows"][0]["id"] == str(join.pk)
    with pytest.raises(ObjectDoesNotExist):
        read(harness, widened, request_id=leave.pk)
    # The SQL selection alone, called as web with the leader as actor.
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT stewardship_ministry_followup_v2(campaign_uuid => %s, "
            "filters => %s::jsonb, actor_uuid => %s, request_uuid => %s)::text",
            [
                harness.campaign.pk,
                json.dumps(FollowupQuery().form_values() | {"assignee": "any"}),
                head,
                leave.pk,
            ],
        )
        outside = json.loads(cursor.fetchone()[0])
        cursor.execute(
            "SELECT stewardship_ministry_followup_v2(campaign_uuid => %s, "
            "filters => %s::jsonb, actor_uuid => %s)::text",
            [
                harness.campaign.pk,
                json.dumps(FollowupQuery().form_values() | {"assignee": "any"}),
                uuid4(),
            ],
        )
        nobody = json.loads(cursor.fetchone()[0])
    assert outside["rows"] == [] and outside["total"] == 0
    assert [item["duid"] for item in outside["ministries"]] == [9]
    assert nobody["authorized"] is False and nobody["rows"] == []
    # SQL wider than Python (Staff in SQL, a leader of 9 in Python) is
    # drift, and the page is refused.
    admin = user("admin@example.org").pk
    with pytest.raises(PermissionError):
        read(harness, Principal(admin, frozenset({"ministry_leader"}), frozenset({9})))


def test_native_queue_detail_and_edit_without_assignment(response_service, google):
    """A leader's real session edits only its Ministry through CSRF POST forms.

    Follow-up has no assignment (#552): no Select column, Assign panel,
    assignee filter, column or field, and the old bulk route is gone.
    """
    harness = setup(response_service)
    browser, head, _, _ = leader(harness, google)
    admin = user("admin@example.org").pk
    join, leave = requests()
    route = reverse("admin:ministry_report")
    absent = (
        b'name="selected"',
        b"Assign selected",
        b"Assigned to",
        b'name="assignee"',
        b"Unassigned",
        b"followup-assign",
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, route)
        assert response.status_code == 200 and response["Cache-Control"] == "no-store"
        assert body.count(b"ministries/follow-up/" + str(join.pk).encode()) == 1
        assert str(leave.pk).encode() not in body
        assert not [marker for marker in absent if marker in body]
        # Identifying filters are private POST state, never a URL.
        assert get(browser, route + "?search=Private")[0].status_code == 400
        # Filtering to one Ministry once revealed its bulk assignment panel.
        response, body = search(browser, route, {"ministry": "9", "stage": "all"})
        assert response.status_code == 200 and b"?search=" not in body
        assert not [marker for marker in absent if marker in body]
        assert b"admin@example.org" not in body and b'datetime=""' not in body
        # The review worklist (#1684) replaced the Status and Outcome
        # filters with Stage; a leader's choices hold Accepted, never the
        # update phase (#1160).
        for kind in ("any", "join", "leave"):
            _, shown = search(browser, route, {"kind": kind, "stage": "all"})
            assert b'<option value="accepted">Accepted (0)</option>' in shown
            assert b'value="to_enter_by_hand"' not in shown
            assert b'name="outcome"' not in shown and b'name="state"' not in shown
        # Shared table (#203): headings choose the selection's own sort values
        # through POST forms carrying the private filters; sorting stays
        # within the leader's scope and any other value is refused. The
        # default newest-first order has no Submitted column to mark (#1064),
        # and oldest first is no longer offered.
        assert b"Page 1 of 1" in body and b"aria-sort=" not in body
        assert b'<a class="sort-link"' not in body
        assert b'name="sort" value="name"' in body
        assert b'value="oldest"' not in body
        response, body = search(
            browser, route, {"stage": "all", "sort": "ministry", "size": "25"}
        )
        assert response.status_code == 200 and str(leave.pk).encode() not in body
        assert b'aria-sort="ascending"' in body
        # A page past the end of the queue shows its last page.
        response, body = search(browser, route, {"stage": "all", "page": "9"})
        assert response.status_code == 200 and b"Page 1 of 1" in body
        assert body.count(b"ministries/follow-up/" + str(join.pk).encode()) == 1
        for invalid in (
            {"sort": "state"},
            {"sort": "-ministry"},
            {"sort": "oldest"},
            {"size": "all"},
            {"assignee": "any"},
            {"state": "assigned"},
            {"stage": "history"},
            {"members": "gone"},
        ):
            assert search(browser, route, invalid)[0].status_code == 400
        _, hidden = search(browser, route, {"ministry": "4"})
        assert b"@example.org" not in hidden
        detail = reverse("admin:ministry_followup_item", args=[join.pk])
        update = reverse("admin:ministry_followup_update", args=[join.pk])
        response, body = get(browser, detail)
        assert response.status_code == 200 and b"No follow-up edits yet." in body
        assert not [marker for marker in absent if marker in body]
        assert b'<option value="assigned"' not in body
        # Another Ministry's or campaign's request is indistinguishable from none.
        assert (
            get(browser, reverse("admin:ministry_followup_item", args=[leave.pk]))[
                0
            ].status_code
            == 403
        )
        form = {
            "expected_version": "1",
            "request_key": str(uuid4()),
            "state": "in_progress",
            "outcome": "",
            "notes": "PRIVATE-NOTE left a message",
            "contact_channel": "phone",
            "contact_date": "2026-01-02",
            "contact_time": "15:04",
            # Filled by ui-v1.js: the time was typed in Pacific time (#558).
            "contact_zone": "America/Los_Angeles",
            "contact_notes": "No answer",
        }
        assert browser.post(update, form).status_code == 403  # No CSRF.
        assert post(browser, update, form).status_code == 302
        assert post(browser, update, form).status_code == 302  # Replay.
        stale = form | {"request_key": str(uuid4())}
        assert post(browser, update, stale).status_code == 409
        for invalid in (
            form | {"request_key": str(uuid4()), "expected_version": "2", **change}
            for change in (
                {"state": "resolved"},
                {"state": "resolved", "outcome": "leave_confirmed"},
                {"state": "cancelled"},
                {"state": "assigned"},
                {"assignee": ""},
                {"assignee": str(head)},
                {"contact_date": "2999-01-01"},
                {"contact_channel": "phone", "contact_date": ""},
                {"contact_zone": ""},  # The portal requires JavaScript (#565).
                {"contact_zone": "Not/A_Zone"},
                {"contact_time": "15:04+00:00"},
                {"unexpected": "field"},
            )
        ):
            # The response-owned transport an in-place refusal streams through.
            assert search(browser, update, invalid)[0].status_code == 400
        # A correctable refusal comes back in place (#553): the same request
        # page, an error summary naming the one problem, the values kept.
        mismatch = form | {
            "request_key": str(uuid4()),
            "expected_version": "2",
            "state": "resolved",
            "outcome": "leave_confirmed",
            "notes": "PRIVATE-NOTE kept <script>alert(1)</script>",
        }
        before = MinistryWorkflowRevision.objects.count()
        response, body = search(browser, update, mismatch)
        assert MinistryWorkflowRevision.objects.count() == before
        assert response.status_code == 400 and b"data-error-summary" in body
        assert b"Left the Ministry doesn&#x27;t apply to a request to join." in body
        assert b'href="#followup-outcome"' in body
        # Kept, and escaped: never live markup.
        assert (
            b"PRIVATE-NOTE kept &lt;script&gt;alert(1)&lt;/script&gt;</textarea>"
            in body
        )
        assert b"<script>alert(1)" not in body
        assert b'name="expected_version" value="2"' in body
        assert b'<option value="resolved" selected>' in body
        assert b'value="2026-01-02"' in body and b"No answer</textarea>" in body
        # A join request is never offered Left the Ministry.
        assert b'<option value="leave_confirmed"' not in body
        assert b'<option value="joined"' in body
        future = form | {
            "request_key": str(uuid4()),
            "expected_version": "2",
            "contact_date": "2999-01-01",
        }
        response, body = search(browser, update, future)
        assert response.status_code == 400
        assert b"date and time can&#x27;t be in the future." in body
        # Local-time entry (#558): a contact time without a usable zone (a
        # tab opened before the change, or none reported) comes back in place,
        # keeping the typed date and time for another try, and a refused page
        # keeps the submitted zone with the other values.
        for zone in ("", "Not/A_Zone"):
            zoneless = form | {
                "request_key": str(uuid4()),
                "expected_version": "2",
                "contact_zone": zone,
            }
            response, body = search(browser, update, zoneless)
            assert response.status_code == 400 and b"data-error-summary" in body
            assert b"came without your computer&#x27;s time zone" in body
            assert b'value="2026-01-02"' in body and b'value="15:04"' in body
        response, body = search(browser, update, mismatch)
        assert b'name="contact_zone" value="America/Los_Angeles"' in body
        # A malformed form still gets the plain error page.
        response, body = search(browser, update, form | {"unexpected": "x"})
        assert response.status_code == 400 and b"could not be read" in body
        response, body = get(browser, detail)
        assert b"PRIVATE-NOTE left a message" in body and b"No answer" in body
        assert b"leader@example.org" in body and b"Phone" in body
        # 3:04 PM Pacific standard time is 23:04 UTC, which the history
        # hands to the browser to show in its own zone.
        stamp = b'datetime="2026-01-02T23:04:00+00:00" data-local-instant'
        assert stamp in body
        # The bulk assignment route is gone.
        bulk = {
            "request_key": str(uuid4()),
            "ministry": "9",
            "assignee": "",
            "selected": f"{join.pk}:2",
        }
        assert post(browser, route + "assign", bulk).status_code == 404
    join.refresh_from_db()
    assert (join.state, join.assignee_id, join.version) == ("in_progress", None, 2)
    assert latest_notes(join.pk) == "PRIVATE-NOTE left a message"
    contacts = MinistryWorkflowRevision.objects.filter(request=join).exclude(
        contact_at=None
    )
    assert list(contacts.values_list("contact_at", flat=True)) == [
        datetime(2026, 1, 2, 23, 4, tzinfo=UTC)
    ]
    # A request assigned before #552, to someone since disabled: the page reads
    # it as New, says nothing of the assignee except in its past history, and
    # saving its status unchanged clears the assignment. That save also logs a
    # contact attempt typed in Tokyo time (UTC+9), its time in a 12-hour form
    # the shared time-of-day entry reads (#398).
    legacy_assign(harness, head, join, admin, notes="Handed over")
    PortalUser.objects.filter(pk=admin).update(disabled=True, version=F("version") + 1)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        _, body = get(browser, detail)
        assert b"can no longer follow up this Ministry" not in body
        assert b"Status: New" in body
        assert b'<option value="new" selected>' in body
        assert b"assigned to admin@example.org" in body  # Past history only.
        assert b'name="assignee"' not in body
        keep = {
            "expected_version": "3",
            "request_key": str(uuid4()),
            "state": "new",
            "outcome": "",
            "notes": "Handed over",
            "contact_channel": "email",
            "contact_date": "2026-01-03",
            "contact_time": "9:30 am",
            "contact_zone": "Asia/Tokyo",
            "contact_notes": "",
        }
        assert post(browser, update, keep).status_code == 302
    join.refresh_from_db()
    assert (join.state, join.assignee_id, join.version) == ("new", None, 4)
    assert contacts.order_by("expected_version").last().contact_at == datetime(
        2026, 1, 3, 0, 30, tzinfo=UTC
    )
    # The page sends only the fields that apply (#553): notes are optional,
    # an open status has no outcome, and no channel means no contact attempt.
    # Without JavaScript every field arrives, and what does not apply is
    # ignored rather than refused.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        minimal = {
            "expected_version": "4",
            "request_key": str(uuid4()),
            "state": "in_progress",
            "notes": "",
            "contact_channel": "",
        }
        assert post(browser, update, minimal).status_code == 302
        stray = minimal | {
            "expected_version": "5",
            "request_key": str(uuid4()),
            "state": "new",
            "outcome": "joined",
            "contact_date": "2026-01-02",
            "contact_time": "10:00",
            "contact_notes": "Typed, then chose no contact attempt",
        }
        assert post(browser, update, stray).status_code == 302
        unresolved = minimal | {
            "expected_version": "6",
            "request_key": str(uuid4()),
            "state": "resolved",
        }
        assert search(browser, update, unresolved)[0].status_code == 400
        closing = unresolved | {
            "request_key": str(uuid4()),
            "state": "closed_no_response",
        }
        assert post(browser, update, closing).status_code == 302
    latest = MinistryWorkflowRevision.objects.filter(request=join).order_by(
        "-expected_version"
    )
    assert [
        (row.state, row.outcome, row.notes, row.contact_channel, row.contact_at)
        for row in latest[:3]
    ] == [
        ("closed_no_response", "no_response", "", None, None),
        ("new", None, "", None, None),
        ("in_progress", None, "", None, None),
    ]
    join.refresh_from_db()
    assert (join.state, join.outcome, join.version) == (
        "closed_no_response",
        "no_response",
        7,
    )
    contexts = str(
        list(
            AuditContext.objects.filter(
                event__event_type__in=[
                    "ministry_request_updated",
                    "ministry_followup_viewed",
                ]
            ).values_list("context", flat=True)
        )
    )
    assert "PRIVATE-NOTE" not in contexts and "No answer" not in contexts


def test_offered_outcomes_are_exactly_those_the_guard_accepts(response_service):
    """Every outcome the form offers a request kind passes the SQL revision
    guard, and the other kind's roster outcome is refused, so the page can
    never offer a choice the database refuses."""
    harness = setup(response_service)
    admin = user("admin@example.org").pk
    join, leave = requests()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        for target, refused in ((join, "leave_confirmed"), (leave, "joined")):
            for outcome in (*outcomes_for(target.action), refused):
                row = dict(
                    request=target,
                    actor_id=admin,
                    request_key=uuid4(),
                    expected_version=target.version,
                    state="resolved",
                    outcome=outcome,
                    notes="Reason",
                )
                if outcome == refused:
                    with (
                        work_transaction(),
                        pytest.raises(
                            DatabaseError, match="not valid for this request"
                        ),
                        transaction.atomic(),
                    ):
                        MinistryWorkflowRevision.objects.create(**row)
                    continue
                with work_transaction(), transaction.atomic():
                    MinistryWorkflowRevision.objects.create(**row)
                    transaction.set_rollback(True)
    # The service refuses each mismatch with its correctable code.
    for target, refused in ((join, "leave_confirmed"), (leave, "joined")):
        with pytest.raises(FollowupRefusal) as mismatch:
            edit(harness, admin, target, state="resolved", outcome=refused)
        assert mismatch.value.code == "outcome_kind"
    assert MinistryWorkflowRevision.objects.count() == 0


def test_refused_save_resubmits_and_a_stale_refusal_is_a_conflict(
    response_service, google
):
    """A refusal found before the version check must not pair stale values
    with the current version: if the request changed meanwhile it answers
    409 and writes nothing. A current refusal's page resubmits normally,
    with its own fresh CSRF token."""
    harness = setup(response_service)
    browser, head, _, _ = leader(harness, google)
    admin = user("admin@example.org").pk
    join, _ = requests()
    update = reverse("admin:ministry_followup_update", args=[join.pk])
    refused = {
        "expected_version": "1",
        "request_key": str(uuid4()),
        "state": "resolved",
        "notes": "A's notes",
        "contact_channel": "",
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(browser, update, refused)
        assert response.status_code == 400
        assert b"Choose an outcome for Resolved." in body
        assert b'name="expected_version" value="1"' in body
        # The outcome is marked in error, its message beside it (#592).
        assert (
            b'<select id="followup-outcome" name="outcome" aria-invalid="true"'
            b' aria-describedby="followup-outcome-tip followup-outcome-error">'
        ) in body
        assert (
            b'<ul class="errorlist" id="followup-outcome-error">'
            b"<li>Choose an outcome for Resolved.</li></ul>"
        ) in body
    assert MinistryWorkflowRevision.objects.count() == 0
    # Someone else saves; A's refused form is now stale.
    edit(harness, admin, join, notes="B's notes")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(browser, update, refused)
        assert response.status_code == 409 and b"This request changed" in body
    assert [row.notes for row in MinistryWorkflowRevision.objects.all()] == [
        "B's notes"
    ]
    # A current refusal, then a resubmit from the page it rendered.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(
            browser,
            update,
            refused | {"expected_version": "2", "request_key": str(uuid4())},
        )
        assert response.status_code == 400
        token = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', body)[1]
        key = re.search(rb'name="request_key" value="([^"]+)"', body)[1]
        fixed = {
            "csrfmiddlewaretoken": token.decode(),
            "expected_version": "2",
            "request_key": key.decode(),
            "state": "resolved",
            "outcome": "joined",
            "notes": "A's notes",
            "contact_channel": "",
        }
        # The form's own token, not the API header the other posts use.
        assert browser.post(update, fixed).status_code == 302
    join.refresh_from_db()
    assert (join.state, join.outcome, join.version) == ("resolved", "joined", 3)
    # A request closed meanwhile is a conflict too, never a dangling form.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = search(
            browser,
            update,
            refused | {"expected_version": "3", "request_key": str(uuid4())},
        )
        assert response.status_code == 409 and b"followup-outcome" not in body
    assert MinistryWorkflowRevision.objects.count() == 2


def test_contact_refusals_mark_their_fields(response_service, google):
    """A contact attempt in the future marks both its date and time in error,
    with the message beside them and the summary linking to the date; one
    with its time missing, or typed in a form the time-of-day entry cannot
    read (#398), marks only the time. Nothing is saved."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google)
    join, _ = requests()
    update = reverse("admin:ministry_followup_update", args=[join.pk])
    contact = {
        "expected_version": "1",
        "state": "in_progress",
        "notes": "",
        "contact_channel": "phone",
        "contact_date": "2099-01-01",
        "contact_time": "10:00",
        "contact_zone": "UTC",
        "contact_notes": "",
    }
    message = (
        "The contact attempt's date and time can't be in the future. "
        "If they aren't, check your computer's clock."
    )
    for values, marked, unmarked, text in (
        (contact, ("contact-date", "contact-time"), (), message),
        (
            contact | {"contact_time": ""},
            ("contact-time",),
            ("contact-date",),
            "Enter the date and time of the contact attempt.",
        ),
        (
            contact | {"contact_time": "25:00"},
            ("contact-time",),
            ("contact-date",),
            "“25:00” has no hour 25: hours run from 0 to 23.",
        ),
    ):
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            response, body = search(
                browser,
                update,
                values | {"request_key": str(uuid4())},
            )
        assert response.status_code == 400
        page = body.decode()
        for field in marked:
            tag = re.search(rf'<input id="{field}"[^>]*>', page)[0]
            assert (
                'aria-invalid="true" aria-describedby="contact-zone-help '
                'contact-details-help contact-error"' in tag
            )
        for field in unmarked:
            assert (
                "aria-invalid" not in re.search(rf'<input id="{field}"[^>]*>', page)[0]
            )
        assert re.search(
            r'<ul class="errorlist" id="contact-error" data-source="\w+">'
            rf"<li>{re.escape(escape(text))}</li></ul>",
            page,
        )
        assert f'<a href="#{marked[0]}">{escape(text)}</a>' in page
    assert MinistryWorkflowRevision.objects.count() == 0


def test_refusal_outside_scope_is_denied_not_a_conflict(response_service, google):
    """A refusal raised before the edit's own scope check must not reveal a
    request outside the caller's Ministries: whatever version is sent, the
    answer is the same denial as for an unknown request, never a 409."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google)
    _, leave = requests()  # Ministry 4, outside the leader's Ministry 9.
    refused = {
        "request_key": str(uuid4()),
        "state": "resolved",
        "notes": "",
        "contact_channel": "",
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        answers = [
            search(
                browser,
                reverse("admin:ministry_followup_update", args=[target]),
                refused | {"expected_version": v},
            )
            for target, v in ((leave.pk, "1"), (leave.pk, "7"), (uuid4(), "1"))
        ]
    assert [response.status_code for response, _ in answers] == [403, 403, 403]
    assert len({body for _, body in answers}) == 1
    assert b"This request changed" not in answers[0][1]
    assert MinistryWorkflowRevision.objects.count() == 0


def _menu_count(body, label):
    """The open count the menu shows after ``label``, or None without one."""
    found = re.search(
        rf'>{label} <span class="admin-menu-count" aria-hidden="true"'
        rf' title="[^"]*">([0-9,]+)</span>'.encode(),
        body,
    )
    return int(found.group(1)) if found else None


def test_menu_shows_open_follow_up_counts_scoped_to_the_viewer(
    response_service, google
):
    """The menu counts each follow-up queue's open items (#585).

    Ministry follow-up counts Unresolved requests: every Ministry for
    Administrators, a Ministry leader's own only. A resolved request leaves
    the count, a zero count shows no number, and Additional information
    counts the Family's current request.
    """
    harness = setup(response_service)
    admin_browser, _ = signed_in()
    # The leader is another Google account, so the Administrator stays one.
    google[0]["sub"] = "synthetic-leader"
    browser, _, _, _ = leader(harness, google)
    admin = user("admin@example.org").pk
    join, leave = requests()
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        body = get(admin_browser, "/admin/")[1]
        assert _menu_count(body, "Ministry requests") == 2
        # This Family wrote no additional information, so nothing is open.
        assert b"Additional information <span" not in body
        assert _menu_count(get(browser, "/admin/")[1], "Ministry requests") == 1
    edit(harness, admin, join, state="resolved", outcome="joined")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert _menu_count(get(admin_browser, "/admin/")[1], "Ministry requests") == 1
        body = get(browser, "/admin/")[1]
        assert b">Ministry requests</a>" in body
        assert _menu_count(body, "Ministry requests") is None
    # Additional information counts the Family's current, uncompleted request;
    # a Ministry leader has no Additional information entry at all.
    form = revisit(harness)
    answers = answers_for(form)
    answers["additional_information"] = "Please call about the choir"
    respond(harness, form, answers)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert (
            _menu_count(get(admin_browser, "/admin/")[1], "Additional information") == 1
        )
        # The menu only: a leader's landing (My review work, #1690) may name
        # other streams in its own text.
        menu = re.search(
            rb'<nav[^>]*aria-label="Administration".*?</nav>',
            get(browser, "/admin/")[1],
            re.S,
        ).group(0)
        assert b"Additional information" not in menu


def test_a_leaders_open_count_costs_the_chrome_one_query(
    response_service, google, monkeypatch
):
    """A Ministry leader whose menu links Ministry follow-up pays exactly one
    more chrome query for its open count, and no other (#585).

    The NAV-2 budget test covers a draft without the Ministry module; this
    is the leader's case with the entry available. The same Home page is
    rendered with and without the count, each from a cold Family portal
    switch cache, and only the chrome's own queries are counted.
    """
    from django.template import engines
    from django.test.utils import CaptureQueriesContext

    from parishkit.stewardship.accounts import admin_context, family_maintenance

    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google)
    engine = engines["django"].engine
    runs = []

    def counted(request):
        """The real chrome, with the statements its one call runs recorded."""
        with CaptureQueriesContext(connection) as chrome:
            result = admin_context.portal_chrome(request)
        if result:
            runs.append([query["sql"] for query in chrome.captured_queries])
        return result

    processors = tuple(
        counted if processor is admin_context.portal_chrome else processor
        for processor in engine.template_context_processors
    )
    monkeypatch.setitem(engine.__dict__, "template_context_processors", processors)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        monkeypatch.setitem(family_maintenance._cache, "at", None)
        body = get(browser, "/admin/")[1]
        assert _menu_count(body, "Ministry requests") == 1
        with monkeypatch.context() as patched:
            patched.setattr(admin_context, "_open_counts", lambda *args: {})
            patched.setitem(family_maintenance._cache, "at", None)
            body = get(browser, "/admin/")[1]
            assert _menu_count(body, "Ministry requests") is None
    counts, plain = runs
    assert len(counts) == len(plain) + 1, (counts, plain)
    marker = "stewardship_ministry_followup_v2"
    assert [marker in sql for sql in counts].count(True) == 1
    assert not any(marker in sql for sql in plain)


def test_menu_ministry_count_applies_the_follow_up_pages_filters(response_service):
    """The menu's Ministry follow-up count is the page's own default total (#585).

    It runs the page's selection, so wherever the page lists requests the
    count is that page's total, and wherever the page cannot list them (an
    unavailable campaign or source, or a leader with no Ministry of this
    campaign in scope) there is no count at all, never a stale number.
    """
    from parishkit.stewardship.accounts import admin_context

    harness = setup(response_service)
    items = [SimpleNamespace(name="ministry_report", url="/admin/follow-up/")]

    def count(principal, campaign_id=harness.campaign.pk):
        """The menu's count for ``principal``, read as the web role reads it."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            return admin_context._open_counts(
                SimpleNamespace(), principal, items, SimpleNamespace(pk=campaign_id)
            ).get("ministry_report")

    staff = actor(harness, ("staff",), ())
    own = actor(harness, ("ministry_leader",), (9,))
    for principal, total in ((staff, 2), (own, 1)):
        assert read(harness, principal)["total"] == total
        assert count(principal) == total
    # A leader of a Ministry this campaign neither selects nor has a request
    # for: the page refuses, and the menu shows no number.
    other = actor(harness, ("ministry_leader",), (77,))
    with pytest.raises(PermissionError):
        read(harness, other)
    assert count(other) is None
    # The count reads its scope in SQL from the actor (#389 L3): Python
    # claiming Staff for a leader cannot widen it, and SQL wider than
    # Python (Staff in SQL, a leader of 9 in Python) is drift the page
    # refuses, so the menu shows no number either.
    assert count(Principal(own.identity, frozenset({"staff"}))) == 1
    narrowed = Principal(staff.identity, frozenset({"ministry_leader"}), frozenset({9}))
    with pytest.raises(PermissionError):
        read(harness, narrowed)
    assert count(narrowed) is None
    # Someone with no portal user reads nothing, whatever Python claims.
    assert count(Principal(uuid4(), frozenset({"staff"}))) is None
    # A campaign the selection cannot read: the page says unavailable, and
    # again the menu shows no number.
    missing = uuid4()
    with (
        pytest.raises(ReadUnavailable),
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        transaction.atomic(),
    ):
        followup_page(missing, FollowupQuery(), staff)
    assert count(staff, missing) is None


def test_a_failing_menu_count_leaves_the_page_and_transaction_usable(
    response_service, google, monkeypatch
):
    """An error in the menu's counts never breaks an Admin page (#585).

    The counts statement is made to fail inside PostgreSQL. The Admin page
    still renders, its menu shows the entries without numbers, and a
    transaction the counts ran in stays usable afterwards, because the
    statement had its own savepoint.
    """
    from parishkit.stewardship.accounts import admin_context

    harness = setup(response_service)
    admin_browser, _ = signed_in()
    staff = actor(harness, ("staff",), ())
    monkeypatch.setattr(
        admin_context, "OPEN_COUNTS", "SELECT 1/0, %(information)s, %(ministry)s"
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(admin_browser, "/admin/")
        assert response.status_code == 200
        assert b">Ministry requests</a>" in body
        assert b"admin-menu-count" not in body
        # Read inside one transaction, as an owning view would: the failed
        # count rolls back only its savepoint.
        items = [SimpleNamespace(name="ministry_report", url="/admin/follow-up/")]
        with transaction.atomic():
            counts = admin_context._open_counts(
                SimpleNamespace(), staff, items, SimpleNamespace(pk=harness.campaign.pk)
            )
            assert counts == {}
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                assert cursor.fetchone() == (1,)


def test_users_read_through_the_actor_and_domain_rules_grant_nothing(
    response_service,
):
    """Staff and leaders read by their user; a hosted domain admits no one (#952).

    The actor-scoped selections derive scope in SQL
    (stewardship_ministry_scope_v1), which since the users swap reads only
    the address-rule projection, each user one row, as the application
    resolves a users configuration. Each Principal here is the application's
    own resolution (current_principal), so the report, the follow-up queue,
    the leader's My review work cards and the menu count all read what they
    did when the application passed the scope; the actor() Administrator
    shows that path is unchanged too. A hosted-domain rule still in a
    sign-in-rules configuration grants nothing in SQL any more, even for a
    proven domain identity (#389 L3's case before the swap).
    """
    from parishkit.stewardship.accounts import admin_context

    harness = setup(response_service)
    store = harness.service.store
    # head@leaders.example leads Food pantry (9) in ParishSoft (#922).
    change(
        store,
        store.active(),
        uuid4(),
        [
            {"operation": "add", "section": "login_rules", **record}
            for record in (
                address("clerk@staff.example", roles=("staff",)),
                address("head@leaders.example", roles=("ministry_leader",)),
                domain("staff.example", roles=("staff",)),
            )
        ],
    )
    staff = current_principal(
        store, user("clerk@staff.example", hosted="staff.example").pk
    )
    head = current_principal(
        store, user("head@leaders.example", hosted="leaders.example").pk
    )
    assert staff.roles == {"staff"} and not staff.ministries
    assert head.roles == {"ministry_leader"} and head.ministries == {9}
    administrator = actor(harness, ("administrator",), ())
    items = [SimpleNamespace(name="ministry_report", url="/admin/follow-up/")]
    for principal, duids, total in (
        (staff, [4, 9], 2),
        (administrator, [4, 9], 2),
        (head, [9], 1),
    ):
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            transaction.atomic(),
        ):
            summary = ministry_page(harness.campaign.pk, MinistryQuery(), principal)
            queue = followup_page(harness.campaign.pk, FollowupQuery(), principal)
            mine = my_ministries(harness.campaign.pk, principal)
            counts = admin_context._open_counts(
                SimpleNamespace(), principal, items, harness.campaign
            )
        assert summary["authorized"] is True
        assert summary["authorization_scope"]["operational"] is (principal is not head)
        assert [row["duid"] for row in summary["summaries"]] == duids
        assert sorted(row["ministry_duid"] for row in queue["rows"]) == duids
        # Staff's To enter by hand count (R39) is Home's, not the menu's.
        counts.pop(admin_context.ENTRY_COUNT, None)
        assert queue["total"] == total and counts == {"ministry_report": total}
        if principal is head:
            assert [(m["duid"], m["join"]) for m in mine["ministries"]] == [(9, 1)]
    # A proven domain identity with no user: SQL reads only users, so even
    # the roles a sign-in-rules configuration's domain rule names in Python
    # admit nothing there.
    domain_only = current_principal(
        store, user("other@staff.example", hosted="staff.example").pk
    )
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        transaction.atomic(),
        pytest.raises(PermissionError),
    ):
        followup_page(
            harness.campaign.pk,
            FollowupQuery(),
            Principal(domain_only.identity, frozenset({"staff"})),
        )


def bulk(harness, actor, versions, action, outcome, *, key=None, **contact):
    """Apply one bulk outcome under the real web database role (#1131)."""
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        return apply_bulk_outcome(
            harness.service.store,
            actor,
            versions,
            action=action,
            outcome=outcome,
            bulk_key=key or uuid4(),
            change=WorkflowChange(state="new", outcome=None, notes="", **contact),
        )


def test_bulk_outcome_is_all_or_none_with_one_revision_per_request(
    response_service, google
):
    """A bulk outcome writes each request's own revision, projection and
    audit event under one correlation id, plus one bulk event; a request of
    the other kind, a stale version or another Ministry's request refuses
    the whole batch and saves nothing (#1131)."""
    harness = setup(response_service)
    _, head, _, _ = leader(harness, google)
    admin = user("admin@example.org").pk
    join, leave = requests()
    # Notes recorded before carry forward on the bulk revision.
    edit(harness, admin, join, notes="PRIVATE-NOTE keep me")
    join.refresh_from_db()
    assert (join.version, leave.version) == (2, 1)
    both = {join.pk: 2, leave.pk: 1}
    # Each bulk outcome is for one kind of request (Administrator,
    # 2026-10-13), declined included: a batch mixing kinds is refused,
    # naming the request of the other kind.
    for action, outcome, wrong in (
        ("join", "joined", leave),
        ("join", "declined", leave),
        ("leave", "leave_confirmed", join),
        ("leave", "declined", join),
    ):
        with pytest.raises(FollowupRefusal) as refused:
            bulk(harness, admin, both, action, outcome)
        assert refused.value.code == "outcome_kind"
        assert refused.value.details["request_id"] == wrong.pk
        assert refused.value.details["kind"] == action
    # A pairing that is not a bulk action is malformed.
    with pytest.raises(ValueError):
        bulk(harness, admin, {leave.pk: 1}, "leave", "joined")
    # One stale version refuses the batch, naming that request.
    with pytest.raises(BulkStale) as stale:
        bulk(harness, admin, {join.pk: 1}, "join", "declined")
    assert stale.value.request_id == join.pk
    # A leader of Ministry 9 cannot reach Ministry 4's request, even mixed
    # with one of their own.
    with pytest.raises(PermissionError):
        bulk(harness, head, both, "join", "declined")
    with pytest.raises(PermissionError):
        bulk(harness, head, {join.pk: 2, uuid4(): 1}, "join", "declined")
    assert MinistryWorkflowRevision.objects.count() == 1
    assert not AuditEvent.objects.filter(
        event_type="ministry_followup_bulk_applied"
    ).exists()
    called, key = database_now() - timedelta(hours=1), uuid4()
    contact = dict(contact_channel="phone", contact_at=called, contact_notes="Hi")
    (declined,) = bulk(
        harness, admin, {join.pk: 2}, "join", "declined", key=key, **contact
    )
    (stayed,) = bulk(harness, admin, {leave.pk: 1}, "leave", "declined", **contact)
    join.refresh_from_db()
    leave.refresh_from_db()
    assert (join.state, join.outcome, join.version) == ("resolved", "declined", 3)
    assert (leave.state, leave.outcome, leave.version) == ("resolved", "declined", 2)
    assert join.resolved_at is not None and leave.resolved_at is not None
    assert declined.notes == "PRIVATE-NOTE keep me"
    assert stayed.notes == ""
    for revision in (declined, stayed):
        assert (revision.contact_channel, revision.contact_at) == ("phone", called)
        assert revision.contact_notes == "Hi"
    # Each action's revisions and events share that action's correlation id.
    assert declined.correlation_id != stayed.correlation_id
    events = AuditEvent.objects.filter(correlation_id=declined.correlation_id)
    assert sorted(events.values_list("event_type", flat=True)) == [
        "ministry_followup_bulk_applied",
        "ministry_request_updated",
    ]
    summary = AuditContext.objects.get(
        event__event_type="ministry_followup_bulk_applied",
        event__correlation_id=declined.correlation_id,
    )
    assert summary.context == {"outcome": "changed", "count": 1, "matching_count": 1}
    assert summary.event.subject_id == harness.campaign.pk
    # Posting the same action again replays it: same revision, no new event.
    (again,) = bulk(
        harness, admin, {join.pk: 2}, "join", "declined", key=key, **contact
    )
    assert again.pk == declined.pk
    assert MinistryWorkflowRevision.objects.count() == 3
    assert (
        AuditEvent.objects.filter(event_type="ministry_followup_bulk_applied").count()
        == 2
    )
    # Closed requests take no further bulk outcome.
    with pytest.raises(BulkStale):
        bulk(harness, admin, {join.pk: 3}, "join", "declined")
    # The request page's history marks the bulk revision, not the single edit.
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        rows, _ = followup_history(join.pk, 1)
    assert [(row.outcome, row.bulk) for row in rows] == [
        ("declined", True),
        (None, False),
    ]


def member_gone(harness, standing):
    """Promote ParishSoft again with Member 3 no longer current (#1217).

    ``standing`` is what stewardship_member_source_status_v1 should then
    report: ``missing`` (deleted, with their contact details and Choir
    role), ``moved`` (now in Family 2), ``inactive`` or ``deceased``. Any
    other value promotes the unchanged source, so Member 3 is current again.
    """
    data = ministry_source()
    if standing == "missing":
        del data.members[3], data.member_contactinfos[3]
        data.ministry_type_memberships[4]["membership"] = []
    elif standing == "moved":
        data.members[3]["familyDUID"] = 2
    elif standing in {"inactive", "deceased"}:
        data.members[3]["memberStatus"] = standing.capitalize()
    snapshot, claim = report_prepare(data)
    promote(snapshot, claim, harness.campaign, harness.rings)


def standing(request_id):
    """stewardship_ministry_request_member_status_v1 under the web role."""
    with (
        task_login(ServiceRole.WEB, exact=True, reconnect=True),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT stewardship_ministry_request_member_status_v1(%s)", [request_id]
        )
        return cursor.fetchone()[0]


@pytest.mark.parametrize(
    "case, mark",
    [
        ("missing", "Not in ParishSoft"),
        ("moved", "Not in ParishSoft"),
        ("inactive", "Inactive in ParishSoft"),
        ("deceased", "Deceased in ParishSoft"),
    ],
)
def test_requests_for_members_no_longer_in_parishsoft_leave_leaders_work(
    response_service, google, case, mark
):
    """A Member ParishSoft drops, moves, deactivates or marks deceased takes
    their requests out of a leader's queue, request pages, counts and My
    Ministries; Staff list them only with Status set to Members no longer
    in ParishSoft, marked; no edit, single or bulk, records anything; and
    when the Member is current again every request is back unchanged
    (#1217)."""
    from parishkit.stewardship.accounts import admin_context

    harness = setup(response_service)
    _, head, _, _ = leader(harness, google)
    admin = user("admin@example.org").pk
    staff = Principal(admin, frozenset({"staff"}), frozenset())
    scoped = Principal(head, frozenset({"ministry_leader"}), frozenset({9}))
    join, leave = requests()
    items = [SimpleNamespace(name="ministry_report", url="/admin/follow-up/")]

    def counts(principal):
        """The menu count and, for the leader, the per-Ministry join count."""
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            transaction.atomic(),
        ):
            menu = admin_context._open_counts(
                SimpleNamespace(), principal, items, harness.campaign
            )["ministry_report"]
            mine = my_ministries(harness.campaign.pk, principal)
        listed = (mine or {}).get("ministries", [])
        return menu, [(entry["duid"], entry["join"]) for entry in listed]

    assert standing(join.pk) == "current"
    assert counts(scoped) == (1, [(9, 1)]) and counts(staff)[0] == 2
    member_gone(harness, case)
    assert standing(join.pk) == standing(leave.pk) == case
    # A leader never sees, counts or opens them.
    assert read(harness, scoped)["total"] == 0
    assert read(harness, scoped, state="any")["total"] == 0
    with pytest.raises(ObjectDoesNotExist):
        read(harness, scoped, request_id=join.pk)
    with pytest.raises(ValueError):
        read(harness, scoped, state="not_in_parishsoft")
    assert counts(scoped) == (0, [(9, 0)])
    # Staff's default view and count match the leader's; the one Status
    # choice lists them, of any status, each marked.
    assert read(harness, staff)["total"] == 0 and counts(staff)[0] == 0
    gone = read(harness, staff, state="not_in_parishsoft")
    assert gone["total"] == 2
    assert {row["source_mark"][0] for row in gone["rows"]} == {mark}
    # The status stays on the row for the shared badge (#1216).
    assert {row["member_status"] for row in gone["rows"]} == {case}
    # A Member the data no longer has in this Family has no name: the page
    # shows the badge alone and a heading names it by its status (#1216).
    known = case in ("inactive", "deceased")
    assert all(bool(row["member_name"]) is known for row in gone["rows"])
    assert {row["member_label"] for row in gone["rows"]} <= (
        {row["member_name"] for row in gone["rows"]} if known else {mark}
    )
    (item,) = read(harness, staff, request_id=join.pk)["rows"]
    assert item["source_mark"][0] == mark and item["open"]
    # The server refuses every edit, single or bulk, by anyone.
    for actor_id in (admin, head):
        with pytest.raises(MemberNotInSource) as refused:
            edit(harness, actor_id, join)
        assert refused.value.request_id == join.pk
    with pytest.raises(MemberNotInSource) as refused:
        bulk(harness, admin, {join.pk: 1}, "join", "joined")
    assert refused.value.request_id == join.pk
    # Nothing was deleted, cancelled, resolved or written.
    assert MinistryWorkflowRevision.objects.count() == 0
    assert sorted(
        MinistryRequest.objects.values_list("state", "version", "outcome")
    ) == [("new", 1, None), ("new", 1, None)]
    # Current again: every request is back as it was, for everyone.
    member_gone(harness, "current")
    assert counts(scoped) == (1, [(9, 1)]) and counts(staff)[0] == 2
    (back,) = read(harness, scoped)["rows"]
    assert back["id"] == str(join.pk) and back["source_mark"] is None
    assert read(harness, staff, state="not_in_parishsoft")["total"] == 0
    edit(harness, head, join)
    join.refresh_from_db()
    assert join.state == "in_progress"


def test_member_standing_reads_the_snapshot_it_is_given(response_service):
    """stewardship_member_source_status_v1 reads one snapshot: an earlier
    (archived campaign's) snapshot keeps the Member current after the
    current one drops them, and a Member the Family added on the form has
    no standing and keeps their requests (#1217)."""
    harness = setup(response_service)
    form = revisit(harness)
    answers = answers_for(form)
    identifier = str(uuid4())
    answers["proposed_members"][identifier] = member(
        first_name="New", last_name="Person", email="proposed@example.org"
    )
    answers["ministries"]["proposed_members"][identifier] = {"join": [9]}
    answers["ministries"]["members"]["3"] = {"join": [9], "leave": [4]}
    respond(harness, form, answers)
    proposed = MinistryRequest.objects.get(entity_kind="proposed_member", state="new")
    family = proposed.submission.family.family_duid

    def current_snapshot():
        """The promoted snapshot the campaign reads now."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT snapshot_id FROM stewardship_source_current")
            return cursor.fetchone()[0]

    def status(snapshot, key="3", family_duid=family):
        """The function under the web role."""
        with (
            task_login(ServiceRole.WEB, exact=True, reconnect=True),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT stewardship_member_source_status_v1(%s,%s,%s)",
                [snapshot, key, family_duid],
            )
            return cursor.fetchone()[0]

    earlier = current_snapshot()
    assert status(earlier) == "current"
    assert status(earlier, family_duid=2) == "moved"
    assert status(earlier, key="999999") == "missing"
    member_gone(harness, "deceased")
    assert status(current_snapshot()) == "deceased"
    assert status(earlier) == "current"
    assert standing(proposed.pk) is None and standing(uuid4()) is None
    staff = Principal(user("admin@example.org").pk, frozenset({"staff"}), frozenset())
    rows = read(harness, staff)["rows"]
    assert [(row["id"], row["source_mark"]) for row in rows] == [
        (str(proposed.pk), None)
    ]


def test_queue_pages_mark_and_refuse_members_no_longer_in_parishsoft(
    response_service, google
):
    """The pages (#1217): a leader's worklist lists none and offers no choice
    for them, and their request pages read as unknown; Staff get the
    off-by-default Members choice, which lists them marked with no box or
    action, and a request page with a notice and no form. A stale single
    save answers 409 and a stale bulk decision says so in the worklist's
    notice (#1684), naming the row for Staff; nothing is saved."""
    harness = setup(response_service)
    browser, _, _, _ = leader(harness, google)
    join, leave = requests()
    route = reverse("admin:ministry_report")
    option = b'<option value="gone"'
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        _, before = get(browser, route)
        detail = reverse("admin:ministry_followup_item", args=[join.pk])
        _, form_page = get(browser, detail)
    request_key = re.search(rb'name="request_key" value="([^"]+)"', form_page)[1]
    member_gone(harness, "inactive")
    rule = address("clerk@example.org", roles=("staff",))
    store = harness.service.store
    change(
        store,
        store.active(),
        uuid4(),
        [{"operation": "add", "section": "login_rules", **rule}],
    )
    google[0]["email"] = "clerk@example.org"
    google[0]["sub"] = "synthetic-clerk-subject"
    clerk, login = signed_in()
    assert login.status_code == 302
    single = {
        "expected_version": "1",
        "request_key": request_key.decode(),
        "state": "in_progress",
        "notes": "",
        "contact_channel": "",
    }
    bulk_form = {
        "stage": "to_review",
        "bulk": "accept",
        "bulk_key": str(uuid4()),
        "bulk_note": "",
        "item": [f"{join.pk}:1"],
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert option not in before
        assert f'value="{join.pk}:1" form="requests-bulk"'.encode() in before
        # The leader: nothing listed, nothing to choose, nothing to open.
        _, body = search(browser, route, {"stage": "all"})
        assert str(join.pk).encode() not in body and option not in body
        assert b"Nothing to review yet." in body
        crafted = search(browser, route, {"members": "gone"})[0]
        assert crafted.status_code == 400
        assert get(browser, detail)[0].status_code == 403
        # A page read before ParishSoft changed: refused, nothing saved.
        update = reverse("admin:ministry_followup_update", args=[join.pk])
        assert post(browser, update, single).status_code == 409
        response, body = search(browser, route, bulk_form)
        assert response.status_code == 200
        assert b"A chosen item: not recorded, changed by someone else." in body
        # Staff: the default queue leaves them out too; the choice lists them.
        _, body = get(clerk, route)
        assert option in body and str(join.pk).encode() not in body
        _, body = search(clerk, route, {"members": "gone"})
        assert option + b" selected" in body
        assert str(join.pk).encode() in body and str(leave.pk).encode() in body
        assert body.count(b"data-source-status=") == 2
        assert b'data-source-status="inactive">Inactive in ParishSoft</span>' in body
        assert b"data-select-row" not in body
        assert b'data-row-action="accept"' not in body
        response, body = get(clerk, detail)
        assert response.status_code == 200
        assert b'id="followup-not-in-source"' in body
        assert (
            b"Inactive in ParishSoft: ParishSoft marks this Member inactive. "
            b"There is nothing to record" in body
        )
        assert b"followup-save" not in body
        assert b"This request is decided." not in body
        response, body = search(clerk, route, bulk_form | {"bulk_key": str(uuid4())})
        assert response.status_code == 200
        assert (
            "Example, Member — Food pantry: not recorded, changed by someone else."
        ).encode() in body
    assert MinistryWorkflowRevision.objects.count() == 0


def test_save_and_next_skips_members_no_longer_in_parishsoft(response_service, google):
    """Save and next opens the next request there is something to record
    for: once ParishSoft marks Member 3 inactive, a request page's next
    request is never one of theirs (#1217)."""
    harness = setup(response_service)
    form = revisit(harness)
    answers = answers_for(form)
    identifier = str(uuid4())
    # "Aardvark, Zed" sorts before "Example, Member", so Member 3's requests
    # follow it in a Member-name view.
    answers["proposed_members"][identifier] = member(
        first_name="Zed", last_name="Aardvark"
    )
    answers["ministries"]["proposed_members"][identifier] = {"join": [9]}
    answers["ministries"]["members"]["3"] = {"join": [9], "leave": [4]}
    respond(harness, form, answers)
    proposed = MinistryRequest.objects.get(entity_kind="proposed_member", state="new")
    member_three = {
        str(pk)
        for pk in MinistryRequest.objects.filter(
            entity_kind="member", state="new"
        ).values_list("pk", flat=True)
    }
    browser, _, _, _ = leader(harness, google, roles=("staff",))
    route = reverse("admin:ministry_report")
    item = reverse("admin:ministry_followup_item", args=[proposed.pk])

    def following():
        """The next request the proposed Member's page offers, or ""."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            _, body = search(browser, route, {"stage": "all", "sort": "name"})
            token = re.search(rb"\?queue=([0-9a-f]+)", body)[1].decode()
            _, page = get(browser, f"{item}?queue={token}")
        return re.search(rb'name="next" value="([^"]*)"', page)[1].decode()

    assert following() in member_three
    member_gone(harness, "inactive")
    assert following() == ""


def test_an_archived_campaigns_request_reads_its_own_snapshot(response_service):
    """A request of an archived campaign reads the campaign's frozen
    snapshot, so a Member ParishSoft dropped later is still current there
    (#1217). The archive is simulated in a rolled-back transaction with the
    guards' triggers off."""
    harness = setup(response_service)
    join, _ = requests()
    with connection.cursor() as cursor:
        cursor.execute("SELECT snapshot_id FROM stewardship_source_current")
        (earlier,) = cursor.fetchone()
    member_gone(harness, "deceased")
    assert standing(join.pk) == "deceased"
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            "UPDATE stewardship_campaign_credentials SET source_snapshot_id=%s "
            "WHERE campaign_id=%s",
            [earlier, harness.campaign.pk],
        )
        assert cursor.rowcount == 1
        cursor.execute(
            "UPDATE stewardship_campaign SET state='archived' WHERE id=%s",
            [harness.campaign.pk],
        )
        cursor.execute(
            "SELECT stewardship_ministry_request_member_status_v1(%s)", [join.pk]
        )
        assert cursor.fetchone()[0] == "current"
        transaction.set_rollback(True)
    assert standing(join.pk) == "deceased"
