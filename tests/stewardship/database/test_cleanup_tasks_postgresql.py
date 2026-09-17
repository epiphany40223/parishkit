"""Actual dispatcher exercises the compiled cleanup owner without real providers."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection
from django.utils import timezone

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import OperationalLog
from parishkit.stewardship.campaigns import cleanup_tasks
from parishkit.stewardship.campaigns.cleanup_requests import begin_cleanup
from parishkit.stewardship.campaigns.cleanup_tasks import TASK_TYPE, cleanup_handler
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    RehearsalCodeReservation,
)
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.production_models import (
    ProductionCleanupTarget,
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import Execution, execute_hint, recover_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status as task_status

from .test_background_grants_postgresql import task_login
from .test_cleanup_batches_postgresql import make_testing_mail
from .test_cleanup_inventory_postgresql import settle_test_receipts
from .test_response_submission_postgresql import form_and_answers, submit
from .test_taskrun_postgresql import act as task_act
from .test_taskrun_postgresql import expire

pytestmark = pytest.mark.django_db(transaction=True)


def queued(harness):
    """Admit only synthetic readiness, with actual inventory and gate ownership."""
    now = timezone.now()
    with work_transaction():
        settle_test_receipts(harness)
    return begin_cleanup(
        campaign_id=harness.campaign.pk,
        request_key=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        readiness_digest="b" * 64,
        acknowledged_at=now,
        reauthenticated_at=now - timedelta(seconds=1),
        admit=lambda *args: True,
    )


def run(status, *, handlers=None):
    """Run a maintained general worker, not the test journal's admission bypass."""
    return execute_hint(
        status.task_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers=handlers or {TASK_TYPE: cleanup_handler()},
    )


@pytest.mark.parametrize("restricted", [False, True])
def test_worker_completes_exact_cleanup_without_activation(
    response_service, restricted
):
    """Census and mail are deleted while durable Production identity remains."""
    form, answers = form_and_answers(response_service)
    answers["members"]["3"]["first_name"] = "Disposable answer"
    submit(response_service, form, answers)
    make_testing_mail(response_service)
    preserved = {
        model: list(model.objects.order_by("pk").values())
        for model in (FamilyCampaign, RehearsalCodeReservation)
    }
    status = queued(response_service)
    if restricted:
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            assert run(status)
    else:
        assert run(status)
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    assert request.state == "cleanup_complete"
    assert request.processed_count == request.inventory_total
    assert not ProductionCleanupTarget.objects.filter(request=request).exists()
    assert TaskRun.objects.get(pk=status.task_id).state == "succeeded"
    assert SystemConfiguration.objects.get().mode == "testing"
    assert Campaign.objects.get(pk=status.campaign_id).state == "draft"
    for model, rows in preserved.items():
        assert list(model.objects.order_by("pk").values()) == rows
    assert not run(status)


def test_scheduler_has_no_cleanup_execution_port():
    """Metadata recovery is not authority to perform deletions."""
    with pytest.raises(PermissionError, match="scheduler cannot execute"):
        cleanup_handler(scheduler=True).execute(None)


def test_cleanup_preserves_production_and_operational_mail(response_service):
    """Preserve live and operational mail while removing Testing content."""
    from parishkit.stewardship.jobs.delivery_states import DeliveryAction
    from parishkit.stewardship.jobs.outbox_models import (
        OutboxEvent,
        OutboxMessage,
        OutboxRender,
    )

    from .test_cleanup_inventory_postgresql import mixed_mail
    from .test_outbox_postgresql import change

    messages = mixed_mail(response_service)
    change(messages["testing_override"], DeliveryAction.CANCEL_UNSENT)
    identifiers = [
        messages[route].message_id for route in ("production", "operational")
    ]
    queries = [
        OutboxMessage.objects.filter(pk__in=identifiers),
        OutboxRender.objects.filter(message_id__in=identifiers),
        OutboxEvent.objects.filter(message_id__in=identifiers),
    ]
    before = [list(query.order_by("pk").values()) for query in queries]
    status = queued(response_service)
    assert run(status)
    assert [list(query.order_by("pk").values()) for query in queries] == before
    assert OutboxMessage.objects.count() == 2


def test_worker_removes_testing_schedule_history_and_fulfillment(response_service):
    """Semantic fulfillment and occurrence history do not outlive Testing detail."""
    from parishkit.stewardship.campaigns.models import (
        ScheduleDefinition,
        ScheduleFulfillment,
        ScheduleOccurrence,
    )
    from parishkit.stewardship.campaigns.schedules import record_fulfillment

    from .campaign_builders import (
        admit_test_work,
        advance,
        campaign_clock,
        claimed_task,
        occurrence,
    )

    actor = uuid4()
    definition = (
        ScheduleDefinition.objects.filter(campaign=response_service.campaign)
        .select_related("current_revision")
        .first()
    )
    with campaign_clock(definition.current_revision.due_at):
        row = occurrence(definition, actor)
        task = claimed_task("schedule_occurrence", row.pk, actor)
        row = advance(row, actor, "running", task_id=task.run_id, fence=task.fence)
        row = advance(row, actor, "succeeded", fence=task.fence)
        fulfillment = record_fulfillment(
            occurrence_id=row.pk,
            disposition="delivered",
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    status = queued(response_service)
    assert run(status)
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    assert request.state == "cleanup_complete"
    assert not ScheduleOccurrence.objects.filter(pk=row.pk).exists()
    assert not ScheduleFulfillment.objects.filter(pk=fulfillment.pk).exists()
    assert ScheduleDefinition.objects.filter(pk=definition.pk).exists()


def test_database_failure_retries_without_partial_deletion(
    response_service, monkeypatch
):
    """Sanitized failure retains the gate, membership and exact prior progress."""
    status = queued(response_service)

    def broken(*args, **kwargs):
        """A synthetic exception must never expose its detail in durable status."""
        raise DatabaseError("private synthetic database detail")

    monkeypatch.setattr(cleanup_tasks, "apply_checkpoint", broken)
    assert run(status)
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    assert request.state == "cleanup_retry_wait"
    assert request.failure_reason == "cleanup_database_failure"
    assert request.processed_count == request.checkpoint_sequence == 0
    assert (
        ProductionCleanupTarget.objects.filter(request=request).count()
        == request.inventory_total
    )
    assert TaskRun.objects.get(pk=status.task_id).state == "retry_wait"
    assert not OperationalLog.objects.filter(event="production_cleanup_failed").exists()


@pytest.mark.parametrize("attempt", [1, 5])
def test_initial_binding_database_failure_records_sanitized_outcome(
    response_service, monkeypatch, attempt
):
    """A rolled-back START still reaches a durable retry or exhausted outcome."""
    status = queued(response_service)
    task = task_status(TaskRun.objects.get(pk=status.task_id))
    for _ in range(attempt - 1):
        task = task_act(task, "claim", lease_seconds=60)
        task = task_act(task, "retryable_failure")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(1.05)")
    original = cleanup_tasks.change_transition
    failed = False

    def fail_once(**options):
        """Fail before the initial domain binding can commit, then recover."""
        nonlocal failed
        if not failed:
            failed = True
            raise DatabaseError("private synthetic binding failure")
        return original(**options)

    monkeypatch.setattr(cleanup_tasks, "change_transition", fail_once)
    assert run(status)
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    assert request.state == ("cleanup_retry_wait" if attempt == 1 else "cleanup_failed")
    assert request.processed_count == 0
    assert TaskRun.objects.get(pk=status.task_id).state == (
        "retry_wait" if attempt == 1 else "failed"
    )


@pytest.mark.parametrize("after_commit", [False, True])
def test_crash_recovery_uses_committed_cleanup_outcome(
    response_service, monkeypatch, after_commit
):
    """A crash after domain completion recovers success without deleting twice."""
    status = queued(response_service)

    def crash(*args, **kwargs):
        """Simulate process loss rather than a retryable database exception."""
        raise RuntimeError("synthetic cleanup crash")

    original = Execution.transition

    def crash_completion(execution, action, **options):
        """Leave all heartbeats/progress unchanged until the final acknowledgment."""
        if action == "complete":
            crash()
        return original(execution, action, **options)

    with monkeypatch.context() as patch:
        if after_commit:
            patch.setattr(Execution, "transition", crash_completion)
        else:
            patch.setattr(cleanup_tasks, "apply_checkpoint", crash)
        with pytest.raises(RuntimeError, match="synthetic cleanup crash"):
            run(status)
    task = task_status(TaskRun.objects.get(pk=status.task_id))
    expire(task_act(task, "heartbeat", lease_seconds=1))
    with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
        assert cleanup_handler(scheduler=True).recover(
            task_status(TaskRun.objects.get(pk=status.task_id))
        ).action == ("recovery_complete" if after_commit else "recovery_retry")
    with task_login(ServiceRole.WORKER, exact=True):
        assert recover_hint(
            status.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: cleanup_handler()},
        )
    task = TaskRun.objects.get(pk=status.task_id)
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    assert task.state == ("succeeded" if after_commit else "retry_wait")
    assert request.state == ("cleanup_complete" if after_commit else "cleanup_running")


def test_exhausted_crash_recovery_keeps_gate_and_records_one_critical(response_service):
    """A crash before initial domain binding fails durably after five claims."""
    from parishkit.stewardship.campaigns.credential_models import (
        CampaignCredentialState,
    )

    status = queued(response_service)
    task = task_status(TaskRun.objects.get(pk=status.task_id))
    for attempt in range(5):
        task = task_act(task, "claim", lease_seconds=1 if attempt == 4 else 60)
        if attempt < 4:
            task = task_act(task, "retryable_failure")
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_sleep(1.05)")
    expire(task)
    options = dict(
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: cleanup_handler()},
    )
    with task_login(ServiceRole.WORKER, exact=True):
        assert recover_hint(status.task_id, **options)
        assert not recover_hint(status.task_id, **options)
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    assert request.state == "cleanup_failed"
    assert request.failure_reason == "cleanup_exhausted"
    assert request.processed_count == 0
    assert CampaignCredentialState.objects.get(
        campaign_id=status.campaign_id
    ).go_live_gate
    assert (
        OperationalLog.objects.filter(
            event="production_cleanup_failed", level="CRITICAL"
        ).count()
        == 1
    )
    from parishkit.stewardship.campaigns.cleanup_requests import retry_cleanup

    retry_options = dict(
        request_id=request.pk,
        command_id=uuid4(),
        expected_version=request.version,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    restarted = retry_cleanup(**retry_options)
    assert restarted.state == "cleanup_queued"
    child = (
        TaskRun.objects.filter(root_id=status.task_id)
        .order_by("-retry_sequence")
        .first()
    )
    assert child.pk != status.task_id
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            child.pk,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: cleanup_handler()},
        )
    assert retry_cleanup(**retry_options).state == "cleanup_complete"
    assert TaskRun.objects.filter(root_id=status.task_id).count() == 2
    assert OperationalLog.objects.filter(event="production_cleanup_failed").count() == 1


def test_explicit_retry_exhaustion_emits_a_new_deduplicated_alert(
    response_service, monkeypatch
):
    """Separate Admin retry chains are separate incidents, not suppressed forever."""
    from parishkit.stewardship.campaigns.cleanup_requests import retry_cleanup

    status = queued(response_service)
    task = task_status(TaskRun.objects.get(pk=status.task_id))
    exhausted_runs = set()

    def broken(*args, **kwargs):
        """Keep the fixture failure provider-free and privately sanitized."""
        raise DatabaseError("synthetic private repeated failure")

    monkeypatch.setattr(cleanup_tasks, "apply_checkpoint", broken)
    for cycle in range(2):
        for _ in range(4):
            task = task_act(task, "claim", lease_seconds=60)
            task = task_act(task, "retryable_failure")
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_sleep(1.05)")
        options = dict(
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: cleanup_handler()},
        )
        assert execute_hint(task.run_id, **options)
        assert not execute_hint(task.run_id, **options)
        exhausted_runs.add(task.run_id)
        assert (
            set(
                OperationalLog.objects.filter(
                    event="production_cleanup_failed"
                ).values_list("pk", flat=True)
            )
            == exhausted_runs
        )
        request = ProductionTransitionRequest.objects.get(pk=status.request_id)
        assert request.state == "cleanup_failed"
        if cycle == 0:
            retry_cleanup(
                request_id=request.pk,
                command_id=uuid4(),
                expected_version=request.version,
                actor_id=uuid4(),
                correlation_id=uuid4(),
                admit=lambda *args: True,
            )
            task = task_status(
                TaskRun.objects.filter(root_id=status.task_id)
                .order_by("-retry_sequence")
                .first()
            )
