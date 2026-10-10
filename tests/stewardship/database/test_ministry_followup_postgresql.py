"""Scoped Ministry follow-up history under the real web role and SQL pairing."""

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

from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.read_guards import ReadUnavailable
from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.reports.ministry_followup import (
    FollowupQuery,
    followup_history,
    followup_page,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.workflows.followup import (
    FollowupRefusal,
    WorkflowChange,
    _system,
    _targets,
    outcomes_for,
    update_request,
)
from parishkit.stewardship.workflows.models import (
    MinistryRequest,
    MinistryWorkflowRevision,
)

from .auth_builders import signed_in, unguarded
from .test_background_grants_postgresql import task_login
from .test_export_views_postgresql import post
from .test_information_followup_postgresql import search
from .test_ministry_exports_postgresql import leader
from .test_ministry_reports_postgresql import page as report_page
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
    everything = read(harness, staff, ministry="9", history="all", state="any")
    assert {item["id"] for item in everything["rows"]} == {
        str(join.pk),
        str(successor.pk),
    }
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        rows, more = followup_history(successor.pk, 1)
        assert [item.notes for item in rows] == ["PRIVATE-NOTE first call"]
        assert not more and followup_history(successor.pk, 2) == ([], False)


def test_native_queue_detail_and_edit_without_assignment(response_service, google):
    """A leader's real session edits only its Ministry through CSRF POST forms.

    Follow-up has no assignment (#552): no Select column, Assign panel,
    assignee filter, column or field, and the old bulk route is gone.
    """
    harness = setup(response_service)
    browser, head, _, _ = leader(harness, google)
    admin = user("admin@example.org").pk
    join, leave = requests()
    route = reverse("admin:ministry_followup")
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
        response, body = search(browser, route, {"ministry": "9", "state": "any"})
        assert response.status_code == 200 and b"?search=" not in body
        assert not [marker for marker in absent if marker in body]
        assert b"admin@example.org" not in body and b'datetime=""' not in body
        # Shared table (#203): headings choose the selection's own sort values
        # through POST forms carrying the private filters; sorting stays
        # within the leader's scope and any other value is refused.
        assert b"Page 1 of 1" in body and b'aria-sort="descending"' in body
        assert b'<a class="sort-link"' not in body
        assert b'name="sort" value="oldest"' in body
        response, body = search(
            browser, route, {"state": "any", "sort": "ministry", "size": "25"}
        )
        assert response.status_code == 200 and str(leave.pk).encode() not in body
        assert b'aria-sort="ascending"' in body
        # A page past the end of the queue shows its last page.
        response, body = search(browser, route, {"state": "any", "page": "9"})
        assert response.status_code == 200 and b"Page 1 of 1" in body
        assert body.count(b"ministries/follow-up/" + str(join.pk).encode()) == 1
        for invalid in (
            {"sort": "state"},
            {"sort": "-ministry"},
            {"size": "all"},
            {"assignee": "any"},
            {"state": "assigned"},
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
        wrong = f"/admin/reports/{uuid4()}/ministries/follow-up/{join.pk}/"
        # Until #145 any campaign but the current one is gone (410).
        assert get(browser, wrong)[0].status_code == 410
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
        assert post(browser, wrong + "update", form).status_code == 410
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
        assert b"Left ministry doesn&#x27;t apply to a request to join." in body
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
        # A join request is never offered Left ministry.
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
            b' aria-describedby="followup-outcome-error">'
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
                'aria-invalid="true" aria-describedby="contact-zone-help contact-error"'
                in tag
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
        assert _menu_count(body, "Ministry follow-up") == 2
        # This Family wrote no additional information, so nothing is open.
        assert b"Additional information <span" not in body
        assert _menu_count(get(browser, "/admin/")[1], "Ministry follow-up") == 1
    edit(harness, admin, join, state="resolved", outcome="joined")
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        assert _menu_count(get(admin_browser, "/admin/")[1], "Ministry follow-up") == 1
        body = get(browser, "/admin/")[1]
        assert b">Ministry follow-up</a>" in body
        assert _menu_count(body, "Ministry follow-up") is None
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
        assert b"Additional information" not in get(browser, "/admin/")[1]


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
        assert _menu_count(body, "Ministry follow-up") == 1
        with monkeypatch.context() as patched:
            patched.setattr(admin_context, "_open_counts", lambda *args: {})
            patched.setitem(family_maintenance._cache, "at", None)
            body = get(browser, "/admin/")[1]
            assert _menu_count(body, "Ministry follow-up") is None
    counts, plain = runs
    assert len(counts) == len(plain) + 1, (counts, plain)
    marker = "stewardship_ministry_followup_v1"
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
    items = [SimpleNamespace(name="ministry_followup", url="/admin/follow-up/")]

    def count(principal, campaign_id=harness.campaign.pk):
        """The menu's count for ``principal``, read as the web role reads it."""
        with task_login(ServiceRole.WEB, exact=True, reconnect=True):
            return admin_context._open_counts(
                SimpleNamespace(), principal, items, SimpleNamespace(pk=campaign_id)
            ).get("ministry_followup")

    staff = Principal(uuid4(), frozenset({"staff"}), frozenset())
    own = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({9}))
    for principal, total in ((staff, 2), (own, 1)):
        assert read(harness, principal)["total"] == total
        assert count(principal) == total
    # A leader of a Ministry this campaign neither selects nor has a request
    # for: the page refuses, and the menu shows no number.
    other = Principal(uuid4(), frozenset({"ministry_leader"}), frozenset({77}))
    with pytest.raises(PermissionError):
        read(harness, other)
    assert count(other) is None
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
    monkeypatch.setattr(
        admin_context, "OPEN_COUNTS", "SELECT 1/0, %(information)s, %(ministry)s"
    )
    with task_login(ServiceRole.WEB, exact=True, reconnect=True):
        response, body = get(admin_browser, "/admin/")
        assert response.status_code == 200
        assert b">Ministry follow-up</a>" in body
        assert b"admin-menu-count" not in body
        # Read inside one transaction, as an owning view would: the failed
        # count rolls back only its savepoint.
        items = [SimpleNamespace(name="ministry_followup", url="/admin/follow-up/")]
        staff = Principal(uuid4(), frozenset({"staff"}), frozenset())
        with transaction.atomic():
            counts = admin_context._open_counts(
                SimpleNamespace(), staff, items, SimpleNamespace(pk=harness.campaign.pk)
            )
            assert counts == {}
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                assert cursor.fetchone() == (1,)
