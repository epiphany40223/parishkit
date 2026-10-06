"""The hourly automation maintenance task, end to end (ADM-11 PR 2).

The real scheduler login produces it, at most once an hour, and the real
general-worker login executes it through the task dispatcher: it deletes
ended Admin sessions and then their command logins, records the endings role
loss implies, and resolves quiet automation incidents.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import connection
from django.db.models import F

from parishkit.stewardship.accounts import automation_maintenance as maintenance
from parishkit.stewardship.accounts import automation_sessions as automation
from parishkit.stewardship.accounts.automation_models import (
    AutomationLogin,
    AutomationSession,
)
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.operational_models import OperationalIncident
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session

from .auth_builders import unguarded
from .automation_builders import command, paired
from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)


def produce(producer):
    """One scheduler pass of the maintenance producer, as the scheduler login."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        return producer(guard)


def consume(identifier):
    """Execute the task as the general worker, through the real dispatcher."""
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        return execute_hint(
            identifier,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={maintenance.TASK_TYPE: maintenance.maintenance_handler()},
        )


def test_the_task_is_produced_once_an_hour(monkeypatch):
    """A second pass in the same hour adds nothing; the next hour adds one."""
    producer = maintenance.MaintenanceProducer()
    first = produce(producer)
    assert len(first) == 1
    assert produce(producer) == ()
    # A fresh producer (a restarted scheduler) finds the same hour's task.
    assert produce(maintenance.MaintenanceProducer()) == first
    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp()")
        now = cursor.fetchone()[0]
    monkeypatch.setattr(maintenance, "database_now", lambda: now + timedelta(hours=1))
    second = produce(producer)
    assert len(second) == 1 and second != first
    assert TaskRun.objects.filter(task_type=maintenance.TASK_TYPE).count() == 2


def test_the_worker_runs_cleanup_endings_and_incident_resolution(auth_service, google):
    """Ended command sessions and their links go; role loss is recorded."""
    _, secret, row = paired(auth_service)
    caller = command(auth_service, secret)
    automation.close_command_session(caller.portal_session)
    with unguarded():
        OperationalIncident.objects.filter(kind="automation_approved").update(
            last_seen=F("last_seen") - timedelta(hours=2),
            first_seen=F("first_seen") - timedelta(hours=2),
            last_notice_at=F("last_notice_at") - timedelta(hours=2),
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE stewardship_address_rule SET roles='[\"staff\"]'::jsonb "
                "WHERE email='admin@example.org'"
            )
    [identifier] = produce(maintenance.MaintenanceProducer())
    assert consume(identifier)
    task = TaskRun.objects.get(pk=identifier)
    assert task.state == "succeeded"
    assert not PortalSession.objects.filter(pk=caller.portal_session.pk).exists()
    assert not AutomationLogin.objects.exists()
    row.refresh_from_db()
    assert row.end_reason == "role_lost"
    assert (
        OperationalIncident.objects.get(kind="automation_approved").resolved_at
        is not None
    )


def test_a_scheduler_cannot_execute_the_task():
    """The scheduler's handler only allocates and recovers."""
    handler = maintenance.maintenance_handler(scheduler=True)
    with pytest.raises(PermissionError):
        handler.execute(None)
    assert AutomationSession.objects.count() == 0


def test_the_worker_prunes_service_status_records_silent_for_a_day():
    """ADM-13: a run removes stale status rows and keeps the rest."""
    from parishkit.stewardship.jobs.service_status_models import ServiceStatus

    ages = {"stale": timedelta(days=1, minutes=1), "fresh": timedelta(hours=23)}
    rows = {}
    for name, age in ages.items():
        rows[name] = uuid4()
        with connection.cursor() as cursor:
            # The schema owner may date a row; logins get the database clock.
            cursor.execute(
                "INSERT INTO stewardship_service_status (id,service,process,"
                "started_at,reported_at,application_version,debug_logging) "
                "VALUES (%s,'scheduler','main',now()-%s,now()-%s,'1.0.0',false)",
                [rows[name], age, age],
            )
    [identifier] = produce(maintenance.MaintenanceProducer())
    assert consume(identifier)
    assert TaskRun.objects.get(pk=identifier).state == "succeeded"
    assert list(ServiceStatus.objects.values_list("pk", flat=True)) == [rows["fresh"]]
