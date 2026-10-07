"""Serious entries say what went wrong, and recovery is logged (#633).

The allowlist, the SQL producers and the recovery trigger are installed by
frozen migration 0008; these tests run against them as installed.
"""

import json
import re
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.jobs.delivery_metadata import alert_counts
from parishkit.stewardship.jobs.operational_content import (
    AUTOMATION_KINDS,
    IncidentKind,
    IncidentLevel,
)
from parishkit.stewardship.jobs.operational_models import OperationalIncident
from parishkit.stewardship.jobs.operational_policy import IncidentPolicy
from parishkit.stewardship.jobs.operational_storage import (
    record_observation,
    record_recovery,
)
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.observability import Event

from ..log_samples import sample as log_sample
from .test_operational_collection_postgresql import consume, schedule

pytestmark = pytest.mark.django_db(transaction=True)

FROZEN = (
    Path(__file__).resolve().parents[3]
    / "src/parishkit/stewardship/schema/migrations/0008_log_detail.sql"
)


def critical_then_collect(event):
    """Record one CRITICAL entry and let the operational intake take it in."""
    log = operational(event, level="CRITICAL", **log_sample(event))
    (identifier,) = schedule()
    assert consume(identifier)
    return log


def recovered():
    """The INFO recovery entries, oldest first."""
    return list(
        OperationalLog.objects.filter(event="incident_recovered").order_by("created_at")
    )


def test_a_resolved_incident_logs_its_recovery_linked_to_the_opening_entry():
    """The INFO entry shares the original's correlation and names it (#633)."""
    opening = critical_then_collect(Event.DUE_WORK_LAG)
    incident = OperationalIncident.objects.get(kind=IncidentKind.SCHEDULER_LAG)
    assert recovered() == []
    record_recovery(IncidentKind.SCHEDULER_LAG)
    incident.refresh_from_db()
    (entry,) = recovered()
    assert (entry.level, entry.schema) == ("INFO", "recovery")
    assert entry.correlation_id == opening.correlation_id
    assert entry.context == {
        "incident_id": str(incident.pk),
        "incident_kind": "scheduler_lag",
        "log_id": str(opening.pk),
        "elapsed_seconds": entry.context["elapsed_seconds"],
        "count": 1,
    }
    assert 0 <= entry.context["elapsed_seconds"] <= 60
    # Resolving again (no open incident) writes nothing more.
    record_recovery(IncidentKind.SCHEDULER_LAG)
    assert len(recovered()) == 1


def test_an_incident_opened_without_a_log_entry_still_logs_its_recovery():
    """Backup and sign-in incidents have no opening entry, so no log_id."""
    with transaction.atomic():
        incident = record_observation(
            IncidentKind.BACKUP_RPO_BREACH,
            IncidentLevel.CRITICAL,
            policy=IncidentPolicy(),
        )
    record_recovery(IncidentKind.BACKUP_RPO_BREACH)
    (entry,) = recovered()
    assert entry.context["incident_id"] == str(incident.pk)
    assert entry.context["incident_kind"] == "backup_rpo_breach"
    assert "log_id" not in entry.context


@pytest.mark.parametrize("kind", AUTOMATION_KINDS)
def test_automation_notices_end_without_a_recovery_entry(kind):
    """They end after an hour without events, which is not a recovery."""
    with transaction.atomic():
        record_observation(kind, IncidentLevel.CRITICAL, policy=IncidentPolicy())
    record_recovery(kind)
    assert OperationalIncident.objects.get(kind=kind).resolved_at is not None
    assert recovered() == []


def test_a_backup_key_change_ends_with_its_follow_up_not_as_recovered():
    """The entry is written; System logs reads it as "Ended" with the check."""
    from parishkit.stewardship.audit.log_details import explain

    with transaction.atomic():
        record_observation(
            IncidentKind.BACKUP_KEY_CHANGED,
            IncidentLevel.CRITICAL,
            policy=IncidentPolicy(),
        )
    record_recovery(IncidentKind.BACKUP_KEY_CHANGED)
    (entry,) = recovered()
    text = explain(entry.schema, entry.context)
    assert text.startswith("Ended: “Backup encryption key changed”")
    assert "kept copy of the private key" in text


def test_the_banner_says_a_problem_ended_only_once_every_entry_is_resolved():
    """Taken in and resolved: ended. Not yet taken in, or still open: not."""
    with transaction.atomic():
        since = database_now() - timedelta(hours=1)
    critical_then_collect(Event.DUE_WORK_LAG)
    _, _, _, ended = alert_counts(since, limit=10)
    assert ended == {}
    record_recovery(IncidentKind.SCHEDULER_LAG)
    resolved = OperationalIncident.objects.get(kind="scheduler_lag").resolved_at
    counts, _, _, ended = alert_counts(since, limit=10)
    assert counts == {"due_work_lag": 1} and ended == {"due_work_lag": resolved}
    # A new failure not yet taken in by the intake means it may be going on.
    operational(Event.DUE_WORK_LAG, level="CRITICAL", **log_sample(Event.DUE_WORK_LAG))
    counts, _, _, ended = alert_counts(since, limit=10)
    assert counts == {"due_work_lag": 2} and ended == {}


def test_a_failed_health_check_names_the_check_and_its_category(monkeypatch):
    """The intake's own failure says which check failed (#633)."""
    from parishkit.stewardship.jobs import operational_collection

    def broken():
        raise DatabaseError("PRIVATE")

    monkeypatch.setattr(operational_collection, "observe_backup_health", broken)
    monkeypatch.setattr(
        operational_collection, "needs_backup_observation", lambda: True
    )
    critical_then_collect(Event.TASK_FAILED)
    entry = OperationalLog.objects.get(level="ERROR", event="task_failed")
    assert entry.schema == "failure"
    assert entry.context["failure"] == "backup_health_check"
    assert entry.context["failure_kind"] == "database_unavailable"
    assert entry.context["outcome"] == "failed"
    assert "PRIVATE" not in str(entry.context)


@pytest.mark.parametrize(
    "schema,context,valid",
    [
        ("failure", {"failure": "provider_status", "status": 503}, True),
        ("failure", {"failure": "smtp_unavailable", "reason": "smtp_transient"}, True),
        ("failure", {"failure_kind": "database_unavailable"}, True),
        ("failure", {"failure": "Provider said no"}, False),
        ("failure", {"failure_kind": "Bad Kind"}, False),
        ("failure", {"reason": "provider text"}, False),
        ("failure", {"status": 999}, False),
        ("failure", {"message": "x"}, False),
        ("recovery", {"incident_kind": "scheduler_lag", "count": 2}, True),
        ("recovery", {"log_id": "not-a-uuid"}, False),
        ("due_work", {"occurrence_id": str(uuid4()), "lag_seconds": 120}, True),
    ],
)
def test_sql_allowlist_admits_exactly_the_new_closed_contexts(schema, context, valid):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT stewardship_safe_context_v1(%s, %s::jsonb)",
            [schema, json.dumps(context)],
        )
        assert cursor.fetchone()[0] is valid


def test_migration_self_check_refuses_an_old_empty_context_producer():
    """The DO block fails if a producer still writes an empty context."""
    text = FROZEN.read_text(encoding="utf-8")
    check = re.search(r"^DO \$check\$.*?^\$check\$;", text, re.M | re.S)[0]
    with connection.cursor() as cursor:
        cursor.execute(check)
        with (
            pytest.raises(DatabaseError, match="was not replaced"),
            transaction.atomic(),
        ):
            cursor.execute(
                "CREATE OR REPLACE FUNCTION public.stewardship_ops_slack_error_v1() "
                "RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER "
                "SET search_path TO pg_catalog,public,pg_temp AS $$ BEGIN "
                "INSERT INTO stewardship_operational_log"
                "(id,actor_id,correlation_id,event,level,schema,context) "
                "VALUES(gen_random_uuid(),NEW.actor_id,NEW.correlation_id,"
                "'task_failed','ERROR','exception','{}'::jsonb); RETURN NULL; END $$"
            )
            cursor.execute(check)
