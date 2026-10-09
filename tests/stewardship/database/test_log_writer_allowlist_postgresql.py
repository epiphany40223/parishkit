"""Each runtime login writes only its own operational log entries (#389 L2).

The writer trigger ``stewardship_operational_log_writer_v1`` gives web,
worker and scheduler, as it gave mail dispatch and backup (#293), a closed
(schema, event, level) list taken from their code's write paths. Every case
inserts as the real login name, with a plain INSERT as the code's raw paths
do; an allowed entry is recorded with the database's own time, anything else
is refused.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.deployment import ServiceRole

from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)

# Contexts the table's safety check accepts for each schema used below.
CONTEXT = {
    "timeout": '{"limit_seconds": 30, "elapsed_seconds": 31}',
    "member_source": '{"family_duid": 1, "member_duid": 2, "field": "email"}',
    "failure": '{"failure": "provider_timeout"}',
    "exception": '{"outcome": "changed"}',
    "task": "{}",
    "schedule": "{}",
    "due_work": "{}",
}

ALLOWED = {
    ServiceRole.WEB: [
        ("timeout", "helper_timed_out", "ERROR"),
        ("member_source", "source_member_unusable", "WARNING"),
        ("failure", "family_engagement_failed", "ERROR"),
    ],
    ServiceRole.WORKER: [
        ("timeout", "task_lease_lost", "WARNING"),
        ("failure", "source_refresh_held", "INFO"),
        ("failure", "source_provider_failed", "WARNING"),
        ("failure", "source_tenant_mismatch", "CRITICAL"),
        ("failure", "source_retention_skipped", "ERROR"),
        ("failure", "task_failed", "CRITICAL"),
        ("exception", "configuration_digest_mismatch", "WARNING"),
        ("task", "fact_drift", "CRITICAL"),
    ],
    ServiceRole.SCHEDULER: [
        ("timeout", "task_timed_out", "WARNING"),
        ("schedule", "source_refresh_held", "INFO"),
        ("due_work", "campaign_boundary_lag", "WARNING"),
        ("due_work", "due_work_lag", "CRITICAL"),
    ],
}

REFUSED = {
    # Web never writes CRITICAL, nor a worker's or scheduler's entries.
    ServiceRole.WEB: [
        ("failure", "family_engagement_failed", "CRITICAL"),
        ("failure", "task_failed", "CRITICAL"),
        ("task", "fact_drift", "CRITICAL"),
        ("due_work", "due_work_lag", "CRITICAL"),
        ("timeout", "task_timed_out", "CRITICAL"),
    ],
    ServiceRole.WORKER: [
        ("failure", "family_engagement_failed", "ERROR"),
        ("due_work", "due_work_lag", "CRITICAL"),
        ("failure", "source_retention_skipped", "CRITICAL"),
    ],
    ServiceRole.SCHEDULER: [
        ("failure", "task_failed", "CRITICAL"),
        ("schedule", "source_refresh_held", "CRITICAL"),
    ],
}


def insert(schema, event, level, *, actor_id=None, created_at=None):
    """One raw insert in its own transaction; returns the new row's id."""
    identifier = uuid4()
    columns = "id, correlation_id, event, level, schema, context, actor_id"
    values = "%s, %s, %s, %s, %s, %s::jsonb, %s"
    arguments = [identifier, uuid4(), event, level, schema, CONTEXT[schema], actor_id]
    if created_at is not None:
        columns += ", created_at"
        values += ", %s"
        arguments.append(created_at)
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO stewardship_operational_log ({columns}) VALUES ({values})",
            arguments,
        )
    return identifier


@pytest.mark.parametrize("role", sorted(ALLOWED, key=str), ids=str)
def test_each_login_records_its_own_entries_only(role):
    """Its listed entries are recorded; anything else is refused."""
    recorded = []
    with task_login(role):
        for entry in ALLOWED[role]:
            recorded.append(insert(*entry))
        for entry in REFUSED[role]:
            with pytest.raises(DatabaseError, match="may not record"):
                insert(*entry)
        # No actor to impersonate, whatever the entry.
        with pytest.raises(DatabaseError, match="may not record"):
            insert(*ALLOWED[role][0], actor_id=uuid4())
        # The database's time, not a backdated one.
        backdated = insert(
            *ALLOWED[role][0], created_at=timezone.now() - timedelta(days=30)
        )
    assert OperationalLog.objects.filter(pk__in=recorded).count() == len(recorded)
    assert OperationalLog.objects.get(pk=backdated).created_at > (
        timezone.now() - timedelta(minutes=5)
    )


def test_mail_and_backup_keep_timeout_entries_only():
    """#293's limits are unchanged: timeout entries, never CRITICAL."""
    for role in (ServiceRole.MAIL_DISPATCH, ServiceRole.BACKUP_WORKER):
        with task_login(role):
            insert("timeout", "task_timed_out", "WARNING")
            for entry in (
                ("timeout", "task_timed_out", "CRITICAL"),
                ("failure", "task_failed", "ERROR"),
            ):
                with pytest.raises(DatabaseError, match="may not record"):
                    insert(*entry)


def test_the_schema_owner_is_not_limited():
    """Migrations, tests and every SECURITY DEFINER writer run as the owner."""
    insert("failure", "family_engagement_failed", "CRITICAL", actor_id=uuid4())


def test_the_scheduler_may_record_the_web_health_alert():
    """#787's web health trigger runs as the scheduler and records
    ``failure/web_unhealthy/CRITICAL``; the writer admits it.

    Until #787 adds the event to the table's vocabulary, the row passes the
    writer and is then refused by the event check, not by the writer.
    """
    with task_login(ServiceRole.SCHEDULER):
        try:
            insert("failure", "web_unhealthy", "CRITICAL")
        except DatabaseError as error:
            assert "operational_event_safe" in str(error), error
        with pytest.raises(DatabaseError, match="may not record"):
            insert("failure", "web_unhealthy", "ERROR")
