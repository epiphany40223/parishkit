"""Scoped Ministry follow-up history under the real web role and SQL pairing."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.ministry_followup import (
    FollowupQuery,
    assignable,
    followup_history,
    followup_page,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.workflows.followup import (
    WorkflowChange,
    assign_requests,
    latest_revision,
    update_request,
)
from parishkit.stewardship.workflows.models import (
    MinistryRequest,
    MinistryWorkflowRevision,
)

from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import post
from .test_information_followup_postgresql import search
from .test_ministry_exports_postgresql import leader
from .test_ministry_reports_postgresql import setup
from .test_ministry_responses_postgresql import respond, revisit
from .test_policy_postgresql import user
from .test_report_workspace_postgresql import read as get
from .test_response_http_postgresql import answers_for

pytestmark = pytest.mark.django_db(transaction=True)


def requests():
    """The harness Family asks Member 3 to join Ministry 9 and leave Ministry 4."""
    rows = MinistryRequest.objects.exclude(state__in=["superseded", "cancelled"])
    return rows.get(ministry_duid=9), rows.get(ministry_duid=4)


def edit(harness, actor, target, *, version=None, key=None, **values):
    """Edit under the real web database role, without migration-owner grants."""
    change = WorkflowChange(
        **(dict(assignee_id=None, state="in_progress", outcome=None, notes="") | values)
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


def test_scoped_history_replay_stale_writers_and_sql_pairing(response_service, google):
    """Leaders edit only their Ministry; SQL rejects every unpaired shortcut."""
    harness = setup(response_service)
    _, head, _, _ = leader(harness, google)
    admin = user("admin@example.org").pk
    outsider = user("outsider@example.org").pk
    join, leave = requests()
    called, key = database_now() - timedelta(hours=1), uuid4()
    values = dict(
        assignee_id=head,
        state="assigned",
        notes="Left a private voicemail",
        contact_channel="phone",
        contact_at=called,
        contact_notes="No answer",
    )
    first = edit(harness, head, join, key=key, **values)
    join.refresh_from_db()
    assert (join.state, join.assignee_id, join.version) == ("assigned", head, 2)
    assert join.outcome is None and join.resolved_at is None
    # A replay returns the same history; a rebound key or stale form does not write.
    assert edit(harness, head, join, version=1, key=key, **values).pk == first.pk
    with pytest.raises(ValueError):
        edit(harness, head, join, version=1, key=key, **(values | {"notes": "Other"}))
    with pytest.raises(StaleRecordError):
        edit(harness, admin, join, version=1, notes="Stale form")
    # Scope is per Ministry for the actor and for whoever is assigned the work.
    with pytest.raises(PermissionError):
        edit(harness, head, leave)
    with pytest.raises(PermissionError):
        edit(harness, outsider, join)
    with pytest.raises(PermissionError):
        edit(harness, head, MinistryRequest(pk=uuid4(), version=1))
    for invalid in (
        dict(assignee_id=outsider, state="assigned"),
        dict(assignee_id=head, state="assigned"),  # Ministry 4 is not the leader's.
    ):
        with pytest.raises(ValueError):
            edit(harness, admin, leave, **invalid)
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
    with (
        task_login(ServiceRole.WORKER, exact=True, reconnect=True),
        pytest.raises(DatabaseError),
        work_transaction(),
    ):
        MinistryWorkflowRevision.objects.create(actor_id=head, **orphan)
    assert MinistryWorkflowRevision.objects.count() == 2


def test_family_resubmission_retains_staff_work(response_service):
    """Same intent inherits the assignee and history; withdrawal ends the workflow."""
    harness = setup(response_service)
    admin = user("admin@example.org").pk
    join, leave = requests()
    noted = edit(
        harness, admin, join, assignee_id=admin, state="assigned", notes="Call again"
    )
    join.refresh_from_db()
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
    assert latest_revision(successor.pk).pk == noted.pk
    assert latest_revision(leave.pk) is None
    for stale in (join, leave):
        with pytest.raises(StaleRecordError):
            edit(harness, admin, stale, notes="Bound to the replaced request")
    chained = edit(
        harness, admin, successor, assignee_id=admin, notes="Reached the Member"
    )
    assert latest_revision(successor.pk).pk == chained.pk
    assert MinistryWorkflowRevision.objects.filter(request=join).count() == 1


def test_bulk_assignment_is_exact_and_atomic(response_service, google):
    """One stale, closed or out-of-scope row leaves every selected row unchanged."""
    harness = setup(response_service)
    _, head, _, _ = leader(harness, google)
    admin = user("admin@example.org").pk
    join, leave = requests()
    store = harness.service.store

    def assign(actor, assignee, versions, key=None):
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            return assign_requests(
                store,
                actor,
                request_key=key or uuid4(),
                assignee_id=assignee,
                versions=versions,
            )

    both = {join.pk: 1, leave.pk: 1}
    for actor, assignee, versions, error in (
        (head, head, both, PermissionError),  # The leader lacks Ministry 4.
        (admin, head, both, ValueError),  # So the leader cannot hold its work.
        (admin, admin, both | {leave.pk: 7}, StaleRecordError),
        (admin, admin, both | {uuid4(): 1}, PermissionError),
        (admin, admin, {}, ValueError),
    ):
        with pytest.raises(error):
            assign(actor, assignee, versions)
    assert MinistryWorkflowRevision.objects.count() == 0
    edit(harness, admin, join, state="in_progress", notes="Keep these notes")
    key = uuid4()
    first = assign(admin, admin, both | {join.pk: 2}, key)
    assert [row.pk for row in assign(admin, admin, both | {join.pk: 2}, key)] == [
        row.pk for row in first
    ]
    join.refresh_from_db()
    leave.refresh_from_db()
    assert (join.state, join.assignee_id) == ("in_progress", admin)
    assert (leave.state, leave.assignee_id) == ("assigned", admin)
    assert latest_revision(join.pk).notes == "Keep these notes"
    assign(admin, None, {leave.pk: leave.version})
    leave.refresh_from_db()
    assert (leave.state, leave.assignee_id) == ("new", None)


def test_work_gate_and_revoked_actor_close_mutation(response_service):
    """A purge gate or disabled account stops edits before any history is written."""
    harness = setup(response_service)
    admin = user("admin@example.org").pk
    join, _ = requests()
    PortalUser.objects.filter(pk=admin).update(disabled=True, version=F("version") + 1)
    with pytest.raises((PermissionError, ObjectDoesNotExist)):
        edit(harness, admin, join, notes="Disabled")
    PortalUser.objects.filter(pk=admin).update(disabled=False, version=F("version") + 1)
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
        assignee_id=head,
        state="assigned",
        notes="PRIVATE-NOTE first call",
        contact_channel="phone",
        contact_at=called,
    )
    staff = Principal(admin, frozenset({"staff"}), frozenset())
    scoped = Principal(head, frozenset({"ministry_leader"}), frozenset({9}))
    nobody = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset())
    page = read(harness, staff)
    assert page["total"] == 2
    # Both requests share one submission instant, so only membership is fixed.
    assert sorted(row["ministry_duid"] for row in page["rows"]) == [4, 9]
    row = read(harness, staff, ministry="9")["rows"][0]
    assert (row["state"], row["assignee_id"], row["version"]) == (
        "assigned",
        str(head),
        2,
    )
    assert row["notes"] == "PRIVATE-NOTE first call"
    assert row["phone_contact_at"] == called and row["email_contact_at"] is None
    # No contact, address or financial payload is ever projected here.
    assert not {"emails", "phones", "address"} & set(row)
    # Closed filter vocabularies select on the current projection.
    assert read(harness, staff, assignee="mine")["total"] == 0
    assert read(harness, scoped, assignee="mine")["total"] == 1
    assert read(harness, staff, assignee=str(head))["total"] == 1
    assert read(harness, staff, assignee="unassigned")["total"] == 1
    assert read(harness, staff, state="assigned", action="join")["total"] == 1
    assert read(harness, staff, action="leave", state="assigned")["total"] == 0
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
        {"assignee": "everyone"},
        {"ministry": "09"},
        {"ministry": "0"},
        {"sort": "random"},
        {"unknown": "x"},
    ):
        with pytest.raises(ValueError):
            FollowupQuery.parse(invalid)
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert {row.pk for row in assignable(9)} >= {admin, head}
        assert head not in {row.pk for row in assignable(4)}
    # A same-intent resubmission keeps the queue row's work, read via the chain.
    form = revisit(harness)
    answers = answers_for(form)
    answers["ministries"]["members"]["3"] = {"join": [9], "leave": [4]}
    respond(harness, form, answers)
    successor = MinistryRequest.objects.get(ministry_duid=9, state="assigned")
    current = read(harness, staff, ministry="9")
    assert [item["id"] for item in current["rows"]] == [str(successor.pk)]
    assert current["rows"][0]["notes"] == "PRIVATE-NOTE first call"
    assert current["rows"][0]["phone_contact_at"] == called
    everything = read(harness, staff, ministry="9", history="all", state="any")
    assert {item["id"] for item in everything["rows"]} == {
        str(join.pk),
        str(successor.pk),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        rows, more = followup_history(successor.pk, 1)
        assert [item.notes for item in rows] == ["PRIVATE-NOTE first call"]
        assert not more and followup_history(successor.pk, 2) == ([], False)


def test_native_queue_detail_edit_and_bulk_assignment(response_service, google):
    """A leader's real session edits only its Ministry through CSRF POST forms."""
    harness = setup(response_service)
    browser, head, _, _ = leader(harness, google)
    join, leave = requests()
    route = f"/admin/reports/{harness.campaign.pk}/ministries/follow-up/"
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(browser, route)
        assert response.status_code == 200 and response["Cache-Control"] == "no-store"
        assert body.count(b"ministries/follow-up/" + str(join.pk).encode()) == 1
        assert str(leave.pk).encode() not in body
        # Identifying filters are private POST state, never a URL.
        assert get(browser, route + "?search=Private")[0].status_code == 400
        response, body = search(browser, route, {"ministry": "9", "state": "any"})
        assert response.status_code == 200 and b'name="selected"' in body
        assert b"?search=" not in body and b"Assign selected" in body
        assert (
            search(browser, route, {"ministry": "4"})[1].count(b'name="selected"') == 0
        )
        detail = route + f"{join.pk}/"
        response, body = get(browser, detail)
        assert response.status_code == 200 and b"No follow-up edits yet." in body
        # Another Ministry's or campaign's request is indistinguishable from none.
        assert get(browser, route + f"{leave.pk}/")[0].status_code == 403
        wrong = f"/admin/reports/{uuid4()}/ministries/follow-up/{join.pk}/"
        assert get(browser, wrong)[0].status_code == 403
        form = {
            "expected_version": "1",
            "request_key": str(uuid4()),
            "state": "assigned",
            "outcome": "",
            "assignee": str(head),
            "notes": "PRIVATE-NOTE left a message",
            "contact_channel": "phone",
            "contact_date": "2026-01-02",
            "contact_time": "15:04",
            "contact_notes": "No answer",
        }
        assert browser.post(detail + "update", form).status_code == 403  # No CSRF.
        assert post(browser, wrong + "update", form).status_code == 403
        assert post(browser, detail + "update", form).status_code == 302
        assert post(browser, detail + "update", form).status_code == 302  # Replay.
        stale = form | {"request_key": str(uuid4())}
        assert post(browser, detail + "update", stale).status_code == 409
        for invalid in (
            form | {"request_key": str(uuid4()), "expected_version": "2", **change}
            for change in (
                {"state": "resolved"},
                {"state": "resolved", "outcome": "leave_confirmed"},
                {"state": "cancelled"},
                {"contact_date": "2999-01-01"},
                {"contact_channel": "", "contact_date": "2026-01-02"},
                {"assignee": "not-a-uuid"},
                {"unexpected": "field"},
            )
        ):
            assert post(browser, detail + "update", invalid).status_code == 400
        response, body = get(browser, detail)
        assert b"PRIVATE-NOTE left a message" in body and b"No answer" in body
        assert b"leader@example.org" in body and b"Phone" in body
        bulk = {
            "request_key": str(uuid4()),
            "ministry": "9",
            "assignee": "",
            "selected": f"{join.pk}:2",
        }
        assert (
            post(browser, route + "assign", bulk | {"ministry": "4"}).status_code == 400
        )
        other = bulk | {"selected": f"{leave.pk}:1", "ministry": "4"}
        assert post(browser, route + "assign", other).status_code == 403
        assert post(browser, route + "assign", bulk).status_code == 302
    join.refresh_from_db()
    assert (join.state, join.assignee_id, join.version) == ("new", None, 3)
    assert latest_revision(join.pk).notes == "PRIVATE-NOTE left a message"
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
