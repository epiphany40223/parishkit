"""Existing critical producers reach durable notice intent without live providers."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from parishkit.stewardship.accounts.auth_incidents import record_incident
from parishkit.stewardship.accounts.auth_models import AuthenticationIncident
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint, execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.operational_collection import (
    BATCH_SIZE,
    TASK_TYPE,
    collection_handler,
    pending_logs,
    produce_collection,
)
from parishkit.stewardship.jobs.operational_models import (
    OperationalIncident,
    OperationalLogReceipt,
    OperationalNotice,
)
from parishkit.stewardship.jobs.operational_policy import IncidentPolicy
from parishkit.stewardship.jobs.operational_sources import critical_auth, critical_log
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.observability import Event

from .test_background_grants_postgresql import task_login

pytestmark = pytest.mark.django_db(transaction=True)


def schedule():
    """The real metadata-only scheduler needs no campaign or provider configuration."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        return produce_collection(guard)


def consume(identifier):
    """Use maintained execution with the exact general-worker SQL identity."""
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        return execute_hint(
            identifier,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: collection_handler()},
        )


def test_python_and_sql_critical_logs_are_consumed_once_with_current_policy(
    settings, monkeypatch
):
    """Exact receipts retain old timestamps without reading private context."""
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(120, 600)
    from parishkit.stewardship.jobs import operational_collection

    # Pin only the producer's minute bucket. Incident and lease clocks stay real;
    # the idempotence assertion must not fail when this test crosses a minute.
    with transaction.atomic():
        instant = database_now()
    monkeypatch.setattr(operational_collection, "database_now", lambda: instant)
    operational(Event.TASK_FAILED, level="CRITICAL")
    with transaction.atomic():
        earlier = database_now() - timedelta(days=1)
    # This uses the same immutable log entry point as SQL-owned domain triggers.
    raw = OperationalLog.objects.create(
        event="production_cleanup_failed",
        level="CRITICAL",
        schema="exception",
        context={},
        created_at=earlier,
    )
    operational(Event.TASK_FAILED, level="CRITICAL")
    operational(Event.TASK_FAILED, level="WARNING")
    identifiers = schedule()
    assert len(identifiers) == 1
    assert schedule() == identifiers
    assert consume(identifiers[0])
    assert OperationalLogReceipt.objects.count() == 3
    assert OperationalLogReceipt.objects.filter(log=raw).exists()
    assert OperationalNotice.objects.count() == 2
    incident = OperationalIncident.objects.get(kind="task_failed")
    assert (
        incident.occurrences,
        incident.suppression_seconds,
        incident.escalation_seconds,
    ) == (2, 120, 600)
    assert not pending_logs().exists()
    assert schedule() == ()
    assert not consume(identifiers[0])
    assert OperationalIncident.objects.get(pk=incident.pk).occurrences == 2
    assert TaskRun.objects.get(pk=identifiers[0]).state == "succeeded"


def test_collection_is_bounded_without_skipping_unconsumed_rows():
    """One shared setup retains every receipt assertion and a real bounded page."""
    OperationalLog.objects.bulk_create(
        [
            OperationalLog(
                event="task_failed", level="CRITICAL", schema="exception", context={}
            )
            for _ in range(BATCH_SIZE + 1)
        ]
    )
    (identifier,) = schedule()
    consume(identifier)
    assert OperationalLogReceipt.objects.count() == BATCH_SIZE
    assert pending_logs().count() == 1
    assert OperationalNotice.objects.count() == 1
    assert OperationalIncident.objects.get().occurrences == BATCH_SIZE


def test_intake_failure_keeps_source_log_and_rolls_back_receipt_and_notice(monkeypatch):
    """A later retry can safely consume the original signal; no partial ACK survives."""
    operational(Event.TASK_FAILED, level="CRITICAL")
    (identifier,) = schedule()
    original = OperationalLogReceipt.objects.create

    def fail(**kwargs):
        """Fail after the SQL notice exists but before the input is acknowledged."""
        original(**kwargs)
        raise RuntimeError("synthetic collector interruption")

    monkeypatch.setattr(OperationalLogReceipt.objects, "create", fail)
    with pytest.raises(RuntimeError):
        consume(identifier)
    assert OperationalLog.objects.count() == 1
    assert not OperationalIncident.objects.exists()
    assert not OperationalNotice.objects.exists()
    assert not OperationalLogReceipt.objects.exists()
    assert TaskRun.objects.get(pk=identifier).state == "running"


def test_receipt_requires_fenced_collector_and_cannot_be_forged():
    """The append-only handoff independently rejects an unrelated/unclaimed task."""
    log = operational(Event.TASK_FAILED, level="CRITICAL")
    (identifier,) = schedule()
    with task_login(ServiceRole.WORKER, exact=True):
        incident = critical_log(Event.TASK_FAILED)
        with pytest.raises(IntegrityError), transaction.atomic():
            OperationalLogReceipt.objects.create(
                log=log,
                incident=incident,
                incident_version=incident.version,
                run_id=identifier,
                fence=1,
                worker_id=uuid4(),
                correlation_id=log.correlation_id,
            )
    assert not OperationalLogReceipt.objects.exists()
    consume(identifier)
    for statement in (
        "DELETE FROM stewardship_ops_log_receipt",
        "UPDATE stewardship_ops_log_receipt SET fence=2",
    ):
        with (
            pytest.raises(IntegrityError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)


def test_scheduler_cannot_consume_or_claim_the_collection():
    """Metadata permission does not grant execution, incident writes or raw context."""
    operational(Event.TASK_FAILED, level="CRITICAL")
    (identifier,) = schedule()
    with task_login(ServiceRole.SCHEDULER, exact=True):
        with pytest.raises(DatabaseError):
            claim_hint(
                identifier,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={TASK_TYPE: collection_handler(scheduler=True)},
            )
        for statement in (
            "SELECT context FROM stewardship_operational_log",
            "INSERT INTO stewardship_ops_log_receipt DEFAULT VALUES",
        ):
            with (
                pytest.raises(DatabaseError) as error,
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
            assert error.value.__cause__.sqlstate == "42501"


def test_auth_outage_and_recovery_have_atomic_notice_intent(settings):
    """The real web producer creates one incident, one recovery and no provider call."""
    settings.STEWARDSHIP_OPERATIONAL_POLICY = IncidentPolicy(60, 120)
    with task_login(ServiceRole.WEB, exact=True):
        record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
        record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
        record_incident("limiter_available", 0, 0, (0, 0, 0, 0))
        record_incident("limiter_available", 0, 0, (0, 0, 0, 0))
    assert AuthenticationIncident.objects.count() == 1
    incident = OperationalIncident.objects.get()
    assert incident.resolved_at is not None and incident.suppression_seconds == 60
    assert list(
        OperationalNotice.objects.order_by("incident_version").values_list(
            "phase", flat=True
        )
    ) == ["opened", "resolved"]


def test_auth_warning_gaps_do_not_create_a_false_sustained_escalation():
    """The existing detector owns continuity; notification does not infer it."""
    with task_login(ServiceRole.WEB, exact=True):
        record_incident("family_abuse", 1, 100, (100, 20, 0, 0))
        record_incident("family_abuse", 1, 200, (100, 20, 0, 0))
        record_incident("family_abuse", 2, 202, (100, 20, 0, 0))
        record_incident("family_abuse", 2, 202, (100, 20, 0, 0))
    assert AuthenticationIncident.objects.count() == 3
    assert OperationalIncident.objects.get().occurrences == 1
    assert OperationalNotice.objects.count() == 1


def test_auth_failure_rolls_back_incident_and_allows_retry(monkeypatch):
    """The detector can retry without losing its notification to an earlier commit."""
    from parishkit.stewardship.jobs import operational_sources

    def fail(kind):
        """Inject after auth intent but before the shared transaction commits."""
        raise RuntimeError("synthetic interruption")

    monkeypatch.setattr(operational_sources, "critical_auth", fail)
    with pytest.raises(RuntimeError):
        record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
    assert not AuthenticationIncident.objects.exists()
    monkeypatch.setattr(operational_sources, "critical_auth", critical_auth)
    record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
    assert OperationalNotice.objects.count() == 1


def test_producer_and_classification_require_compiled_types():
    """Untrusted UUIDs, event strings or truthy role flags are not authority."""
    with pytest.raises(PermissionError):
        produce_collection(uuid4())
    with pytest.raises(TypeError):
        collection_handler(scheduler=1)
    with pytest.raises(TypeError):
        critical_log("private@example.test")
    with pytest.raises(ValueError):
        critical_auth("private@example.test")
    assert schedule() == ()
