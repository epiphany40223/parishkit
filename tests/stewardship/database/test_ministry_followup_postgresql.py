"""Scoped Ministry follow-up history under the real web role and SQL pairing."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.policy_models import PortalUser
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditContext
from parishkit.stewardship.campaigns.runtime_models import CampaignWorkGate
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
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
from .test_ministry_exports_postgresql import leader
from .test_ministry_reports_postgresql import setup
from .test_ministry_responses_postgresql import respond, revisit
from .test_policy_postgresql import user
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
