"""Actual fenced SMTP outcomes own durable alerts and observed recovery."""

import json
from dataclasses import replace
from datetime import timedelta
from threading import Event as WaitEvent
from uuid import uuid4

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction
from psycopg import sql

from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns.schedule_models import ScheduleDefinition
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult, ProviderHealth
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs import operational_collection
from parishkit.stewardship.jobs.mail_health import observe_mail_health
from parishkit.stewardship.jobs.operational_models import (
    OperationalIncident,
    OperationalNotice,
    OperationalRecipient,
)
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.observability import Event

from .campaign_builders import campaign_clock, change
from .test_background_grants_postgresql import task_login
from .test_family_mail_dispatch_postgresql import prepare
from .test_family_mail_preparation_postgresql import family_mail as family_mail_fixture
from .test_family_mail_worker_postgresql import (
    deliver,
)
from .test_family_mail_worker_postgresql import (
    dispatch_worker as dispatch_worker_fixture,
)
from .test_operational_collection_postgresql import consume, schedule
from .test_operational_dispatch_postgresql import (
    begin as begin_operational,
)
from .test_operational_dispatch_postgresql import (
    claim as claim_operational,
)
from .test_operational_dispatch_postgresql import (
    finish_submission as finish_operational,
)
from .test_operational_fanout_postgresql import consume as fanout
from .test_operational_fanout_postgresql import schedule as schedule_fanout

pytestmark = pytest.mark.django_db(transaction=True)
family_mail = family_mail_fixture
dispatch_worker = dispatch_worker_fixture


@pytest.fixture
def mail_run(dispatch_worker, monkeypatch):
    """Reuse one setup; replace only external submission and retry delay."""
    harness, path = dispatch_worker
    outcomes = []

    def submit(*args, **kwargs):
        """The real maintained owner has already committed its exact submission."""
        kwargs["check"]()
        return outcomes.pop(0)

    monkeypatch.setattr(
        "parishkit.stewardship.jobs.family_mail_delivery_tasks.submit_family", submit
    )
    for module in ("family_mail_dispatch", "family_mail_delivery_tasks"):
        monkeypatch.setattr(
            f"parishkit.stewardship.jobs.{module}.retry_delay", lambda attempt: 1
        )
    with campaign_clock(ScheduleDefinition.objects.get().current_revision.due_at):
        message = prepare(harness)

        def send(result):
            """Restart the process circuit, but retain all real SQL attempt history."""
            message.refresh_from_db()
            if message.state == "retry_wait":
                WaitEvent().wait(1.05)
            outcomes.append(result)
            deliver(harness, path, message)
            assert not outcomes
            message.refresh_from_db()

        yield harness, message, send


@pytest.fixture
def collect(monkeypatch):
    """Advance only collector idempotency buckets, never leases or provider clocks."""
    with work_transaction():
        instant = database_now()
    tick = [0]

    def run():
        tick[0] += 1
        monkeypatch.setattr(
            operational_collection,
            "database_now",
            lambda: instant + timedelta(minutes=tick[0]),
        )
        (identifier,) = schedule()
        assert consume(identifier)
        assert not consume(identifier)

    return run


def observe():
    """Use actual general-worker rights under the shared outcome/sampler lock."""
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        observe_mail_health()


@pytest.mark.parametrize("status", [Status.SYSTEMIC, Status.UNKNOWN])
def test_systemic_outcome_commits_critical_intent_with_exact_result(
    mail_run, collect, status
):
    """Unknown DATA acceptance can still carry a definitive systemic health fault."""
    _, message, send = mail_run
    send(FamilyDeliveryResult(status, 1, health=ProviderHealth.SYSTEMIC))
    assert message.state in {"permanent_failure", "delivery_unknown"}
    log = OperationalLog.objects.get(event=Event.MAIL_PROVIDER_FAILED)
    assert log.level == "CRITICAL"
    assert log.context == {"task_id": str(message.task_id)}
    collect()
    incident = OperationalIncident.objects.get(kind="mail_provider_unavailable")
    assert incident.resolved_at is None and incident.occurrences == 1
    assert OperationalNotice.objects.filter(incident=incident).count() == 1


def test_restart_cannot_erase_outages_and_unobserved_cannot_recover(mail_run, collect):
    """Three real unavailable outcomes alert, despite a fresh circuit each send."""
    _, _, send = mail_run
    for index in range(3):
        send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
        assert OperationalLog.objects.filter(
            event=Event.MAIL_PROVIDER_FAILED, level="CRITICAL"
        ).count() == int(index == 2)
    collect()
    incident = OperationalIncident.objects.get(kind="mail_provider_unavailable")
    send(FamilyDeliveryResult(Status.TRANSIENT, 1, health=ProviderHealth.UNOBSERVED))
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is None
    # Recipient refusal proves the transport works, not that this Family was sent.
    send(FamilyDeliveryResult(Status.PERMANENT, 1, permanent=(0,)))
    observe()
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is not None
    assert list(
        OperationalNotice.objects.filter(incident=incident)
        .order_by("incident_version")
        .values_list("phase", flat=True)
    ) == ["opened", "resolved"]


def test_delayed_intake_preserves_outage_then_recovers_from_new_success(
    mail_run, collect
):
    """Fast recovery cannot delete a critical event before its first collection."""
    _, _, send = mail_run
    for _ in range(3):
        send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
    send(FamilyDeliveryResult(Status.ACCEPTED, 1))
    observe()
    assert not OperationalIncident.objects.filter(
        kind="mail_provider_unavailable"
    ).exists()
    collect()
    incident = OperationalIncident.objects.get(kind="mail_provider_unavailable")
    assert incident.resolved_at is not None
    assert list(
        OperationalNotice.objects.filter(incident=incident)
        .order_by("incident_version")
        .values_list("phase", flat=True)
    ) == ["opened", "resolved"]


def test_old_healthy_provider_does_not_resolve_new_outage(mail_run, collect):
    """A healthy recipient refusal before later failed connections is stale proof."""
    _, _, send = mail_run
    send(FamilyDeliveryResult(Status.TRANSIENT, 1, transient=(0,)))
    for _ in range(3):
        send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
    collect()
    incident = OperationalIncident.objects.get(kind="mail_provider_unavailable")
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is None


def test_unrelated_configuration_edit_preserves_failure_streak(mail_run, collect):
    """Provider identity depends on credentials/settings, not the YAML version UUID."""
    harness, _, send = mail_run
    send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
    version = harness.service.store.active()
    parish = version.document()["sections"]["parish"][0]
    assert (
        change(
            harness.service.store,
            version,
            uuid4(),
            [
                {
                    "operation": "update",
                    "section": "parish",
                    "id": parish["id"],
                    "values": {"name": "Updated Example"},
                }
            ],
        ).state
        == "applied"
    )
    for _ in range(2):
        send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
    collect()
    assert (
        OperationalIncident.objects.get(kind="mail_provider_unavailable").resolved_at
        is None
    )


def test_health_sampling_does_not_depend_on_source_admission(
    mail_run, collect, monkeypatch
):
    """Operational recovery can still run while ordinary source work is held."""
    _, _, send = mail_run
    for _ in range(3):
        send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
    collect()
    send(FamilyDeliveryResult(Status.ACCEPTED, 1))
    monkeypatch.setattr(operational_collection, "admitted_source_scope", lambda: None)
    collect()
    assert (
        OperationalIncident.objects.get(kind="mail_provider_unavailable").resolved_at
        is not None
    )


def test_provider_configuration_change_requires_new_healthy_proof(mail_run, collect):
    """Old account success cannot certify a newly configured delegated identity."""
    harness, _, send = mail_run
    for _ in range(3):
        send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
    collect()
    send(FamilyDeliveryResult(Status.TRANSIENT, 1, transient=(0,)))
    version = harness.service.store.active()
    workspace = next(
        item
        for item in version.document()["sections"]["integrations"]
        if item["values"]["kind"] == "google_workspace"
    )
    assert (
        change(
            harness.service.store,
            version,
            uuid4(),
            [
                {
                    "operation": "update",
                    "section": "integrations",
                    "id": workspace["id"],
                    "values": {"settings": {"delegated_email": "changed@example.org"}},
                }
            ],
        ).state
        == "applied"
    )
    observe()
    incident = OperationalIncident.objects.get(kind="mail_provider_unavailable")
    assert incident.resolved_at is None
    send(FamilyDeliveryResult(Status.ACCEPTED, 1))
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is not None


def test_result_and_critical_intent_rollback_together(mail_run, monkeypatch):
    """Failed result persistence leaves uncertainty, never an unalerted final result."""
    from parishkit.stewardship.jobs import family_mail_dispatch
    from parishkit.stewardship.jobs.outbox_models import OutboxEvent

    _, message, send = mail_run
    original = family_mail_dispatch.change_message

    def interrupted(*args, **kwargs):
        """Interrupt after critical insertion but before result commit."""
        result = original(*args, **kwargs)
        # The trigger has inserted intent before the owning transaction commits.
        # This callback runs under MAIL and deliberately cannot read that log.
        if kwargs["action"].value == "fail_unaccepted":
            raise RuntimeError("Synthetic health journal interruption")
        return result

    monkeypatch.setattr(family_mail_dispatch, "change_message", interrupted)
    with pytest.raises(RuntimeError, match="Synthetic"):
        send(FamilyDeliveryResult(Status.SYSTEMIC, 1))
    message.refresh_from_db()
    assert message.state == "submitting"
    assert not OutboxEvent.objects.filter(
        message=message, reason="smtp_systemic"
    ).exists()
    assert not OperationalLog.objects.filter(event=Event.MAIL_PROVIDER_FAILED).exists()


def test_scheduler_stops_mail_only_sampling_after_recovery(
    mail_run, collect, monkeypatch
):
    """Historical sends do not create perpetual no-op Tasks after recovery."""
    _, _, send = mail_run
    monkeypatch.setattr(operational_collection, "admitted_source_scope", lambda: None)
    send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
    assert schedule() == ()
    for _ in range(2):
        send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
    collect()
    send(FamilyDeliveryResult(Status.ACCEPTED, 1))
    collect()
    assert schedule() == ()


def test_mail_role_cannot_forge_or_read_operational_logs(mail_run):
    """Only the guarded result trigger can emit mail health intent for MAIL."""
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        for statement in (
            "SELECT created_at FROM stewardship_operational_log",
            "INSERT INTO stewardship_operational_log "
            "(id,correlation_id,event,level,schema,context) "
            "VALUES(gen_random_uuid(),gen_random_uuid(),'mail_provider_failed',"
            "'CRITICAL','none','{}')",
            "SELECT stewardship_mail_health_result_v1()",
        ):
            with (
                pytest.raises(ProgrammingError) as denied,
                transaction.atomic(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
            assert denied.value.__cause__.sqlstate == "42501"


def test_invalid_result_cannot_enter_health_history(mail_run, monkeypatch):
    """Existing SQL guards prevent malformed evidence becoming historical poison."""
    from parishkit.stewardship.jobs import family_mail_dispatch

    _, message, send = mail_run
    original = family_mail_dispatch.result_evidence
    with monkeypatch.context() as patch:
        patch.setattr(
            family_mail_dispatch,
            "result_evidence",
            lambda *args, **kwargs: replace(
                original(*args, **kwargs), evidence_digest="a" * 64
            ),
        )
        with pytest.raises(IntegrityError, match="Family provider result is invalid"):
            send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
    message.refresh_from_db()
    assert message.state == "submitting"
    assert not OperationalLog.objects.filter(event=Event.MAIL_PROVIDER_FAILED).exists()


def test_pending_critical_receipt_fences_recovery(mail_run, collect):
    """An already open episode cannot resolve ahead of another retained failure."""
    _, _, send = mail_run
    for _ in range(3):
        send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
    collect()
    send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
    assert OperationalLog.objects.filter(event=Event.MAIL_PROVIDER_FAILED).count() == 2
    send(FamilyDeliveryResult(Status.ACCEPTED, 1))
    observe()
    incident = OperationalIncident.objects.get(kind="mail_provider_unavailable")
    assert incident.resolved_at is None
    collect()
    incident.refresh_from_db()
    assert incident.resolved_at is not None and incident.occurrences == 2


@pytest.mark.parametrize("operational_status", [Status.ACCEPTED, Status.UNAVAILABLE])
def test_operational_results_participate_in_campaign_failure_streak(
    mail_run, operational_status
):
    """Only campaign sends originate alerts, but all provider observations count."""
    from parishkit.stewardship.jobs.operational_content import (
        IncidentKind,
        IncidentLevel,
    )
    from parishkit.stewardship.jobs.operational_policy import IncidentPolicy
    from parishkit.stewardship.jobs.operational_storage import record_observation

    harness, _, send = mail_run
    send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        incident = record_observation(
            IncidentKind.SOURCE_STALE, IncidentLevel.CRITICAL, policy=IncidentPolicy()
        )
    for identifier in schedule_fanout():
        assert fanout(identifier, harness.service.store)
    recipient = OperationalRecipient.objects.select_related("outbox").get(
        cohort__notice__incident_id=incident.pk
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim_operational(recipient.outbox, harness.service.store)
        begin_operational(recipient.outbox, execution, harness.service.store)
        finish_operational(
            recipient.outbox_id,
            execution.claim,
            FamilyDeliveryResult(operational_status, 1),
        )
    for _ in range(2):
        send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
    assert OperationalLog.objects.filter(event=Event.MAIL_PROVIDER_FAILED).exists() is (
        operational_status is Status.UNAVAILABLE
    )


@pytest.mark.parametrize("history", ["foreign_provider", "unobserved", "unhealthy"])
def test_provider_history_queries_remain_bounded(mail_run, history):
    """Exercise our actual query/index contract with a sparse current provider.

    The transaction-local replica is a query-plan fixture, not admitted outbox
    history. It preserves current column/index definitions without bypassing
    authoritative delivery guards or spending 20,000 real provider attempts.
    """
    from parishkit.stewardship.jobs.family_mail_results import result_evidence
    from parishkit.stewardship.jobs.mail_health import (
        health_filter,
        observed_results,
        provider_identity,
    )
    from parishkit.stewardship.jobs.outbox_models import OutboxEvent

    _, message, send = mail_run
    send(FamilyDeliveryResult(Status.TRANSIENT, 1, transient=(0,)))
    event = OutboxEvent.objects.get(message=message, reason="smtp_transient")
    assert event.provider_identity == provider_identity(event.render.configuration_id)
    assert event.provider_identity
    assert (
        OutboxEvent.objects.get(message=message, action="created").provider_identity
        == ""
    )
    background = (
        FamilyDeliveryResult(Status.TRANSIENT, 1, health=ProviderHealth.UNOBSERVED)
        if history == "unobserved"
        else FamilyDeliveryResult(Status.UNAVAILABLE, 1)
    )
    evidence = result_evidence(background, semantic_key=message.semantic_key)
    columns = [field.column for field in OutboxEvent._meta.concrete_fields]
    overrides = {
        "id": sql.SQL("gen_random_uuid()"),
        "command_id": sql.SQL("gen_random_uuid()"),
        "version": sql.SQL("1000 + series.n"),
        "created_at": sql.SQL("e.created_at - series.n * interval '1 second'"),
        "provider_identity": sql.Literal(
            "f" * 64 if history == "foreign_provider" else event.provider_identity
        ),
        "evidence_note": sql.Literal(evidence.evidence_note),
        "evidence_digest": sql.Literal(evidence.evidence_digest),
        "reason": sql.Literal(evidence.reason),
    }
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE health_probe "
            "(LIKE stewardship_outbox_event INCLUDING ALL) ON COMMIT DROP"
        )
        names = sql.SQL(",").join(map(sql.Identifier, columns))
        values = sql.SQL(",").join(
            overrides.get(name, sql.Identifier("e", name)) for name in columns
        )
        cursor.execute(
            sql.SQL(
                "INSERT INTO health_probe ({}) SELECT {} "
                "FROM stewardship_outbox_event WHERE id=%s"
            ).format(names, names),
            [event.pk],
        )
        cursor.execute(
            sql.SQL(
                "INSERT INTO health_probe ({}) SELECT {} "
                "FROM stewardship_outbox_event e "
                "CROSS JOIN generate_series(1,20000) series(n) WHERE e.id=%s"
            ).format(names, values),
            [event.pk],
        )
        cursor.execute("ANALYZE health_probe")
        results = observed_results(event.render.configuration_id)
        if history == "unhealthy":
            results = results.filter(health_filter((ProviderHealth.HEALTHY,)))
        query, params = results[:3].query.sql_with_params()
        query = query.replace('"stewardship_outbox_event"', '"health_probe"')
        cursor.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + query, params)
        plan = cursor.fetchone()[0][0]["Plan"]

        def visited(node):
            """Count only our outcome relation's scanned and filtered rows."""
            total = 0
            if node.get("Relation Name") == "health_probe":
                total = (
                    node.get("Actual Rows", 0) + node.get("Rows Removed by Filter", 0)
                ) * node.get("Actual Loops", 1)
            return total + sum(visited(child) for child in node.get("Plans", []))

        assert plan["Actual Rows"] == 1
        assert visited(plan) < 100, json.dumps(plan)
        # Exercise the actual installed trigger query, not a hand-copied facsimile.
        cursor.execute(
            "SELECT pg_get_functiondef("
            "'stewardship_mail_health_result_v1()'::regprocedure)"
        )
        definition = cursor.fetchone()[0]
        candidate_query = definition.split("FOR candidate IN", 1)[1].split("LOOP", 1)[0]
        candidate_query = (
            candidate_query.replace("%", "%%")
            .replace("public.stewardship_outbox_event", "pg_temp.health_probe")
            .replace("NEW.provider_identity", "%s")
        )
        cursor.execute(
            "EXPLAIN (ANALYZE, FORMAT JSON) " + candidate_query,
            [event.provider_identity],
        )
        plan = cursor.fetchone()[0][0]["Plan"]
        assert plan["Actual Rows"] == (3 if history == "unhealthy" else 1)
        assert visited(plan) < 100, json.dumps(plan)


def test_operational_failure_vetoes_old_recovery_without_recursive_alert(
    mail_run, collect
):
    """A failed alert send is actual provider evidence, not a new email alert source."""
    harness, _, send = mail_run
    for _ in range(3):
        send(FamilyDeliveryResult(Status.UNAVAILABLE, 1))
    collect()
    send(FamilyDeliveryResult(Status.TRANSIENT, 1, transient=(0,)))
    for identifier in schedule_fanout():
        assert fanout(identifier, harness.service.store)
    recipient = OperationalRecipient.objects.select_related("outbox").get(
        cohort__notice__incident__kind="mail_provider_unavailable"
    )
    with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
        execution = claim_operational(recipient.outbox, harness.service.store)
        begin_operational(recipient.outbox, execution, harness.service.store)
        finish_operational(
            recipient.outbox_id,
            execution.claim,
            FamilyDeliveryResult(Status.SYSTEMIC, 1),
        )
    assert (
        OperationalLog.objects.filter(
            event=Event.MAIL_PROVIDER_FAILED, level="CRITICAL"
        ).count()
        == 1
    )
    observe()
    incident = OperationalIncident.objects.get(kind="mail_provider_unavailable")
    assert incident.resolved_at is None
    send(FamilyDeliveryResult(Status.ACCEPTED, 1))
    observe()
    incident.refresh_from_db()
    assert incident.resolved_at is not None
