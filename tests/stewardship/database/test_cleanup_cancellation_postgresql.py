"""Admin cancellation is durable intent, not impersonation of a live worker."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.campaigns import cleanup_tasks
from parishkit.stewardship.campaigns.cleanup_requests import request_cancellation
from parishkit.stewardship.campaigns.cleanup_tasks import TASK_TYPE, cleanup_handler
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.production_models import (
    ProductionCleanupCancellation,
    ProductionCleanupTarget,
    ProductionTransitionRequest,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import Execution, claim_hint, recover_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status as task_status

from .test_background_grants_postgresql import task_login
from .test_cleanup_tasks_postgresql import queued, run
from .test_taskrun_postgresql import act as task_act
from .test_taskrun_postgresql import expire

pytestmark = pytest.mark.django_db(transaction=True)


def cancel(request_id):
    """Synthetic Admin owns intent; the actual worker still owns stopping itself."""
    request = ProductionTransitionRequest.objects.get(pk=request_id)
    return request_cancellation(
        request_id=request.pk,
        command_id=uuid4(),
        expected_version=request.version,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )


def assert_cancelled(status):
    """Cancelling releases only this gate and retains the recorded cleanup result."""
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    assert request.state == "cancelled"
    assert not CampaignCredentialState.objects.get(
        campaign_id=request.campaign_id
    ).go_live_gate
    assert TaskRun.objects.get(pk=status.task_id).state == "cancelled"
    return request


def test_queued_cancellation_is_immediate_and_does_not_delete(response_service):
    """There is no worker fence to impersonate before the task is claimed."""
    status = queued(response_service)
    cancel(status.request_id)
    request = assert_cancelled(status)
    assert request.processed_count == 0
    assert (
        ProductionCleanupTarget.objects.filter(request=request).count()
        == request.inventory_total
    )


@pytest.mark.parametrize("crashed", [False, True])
def test_running_cancellation_is_observed_by_worker_or_recovery(
    response_service, crashed
):
    """An Admin cannot cancel an in-flight claim directly; a boundary finishes it."""
    status = queued(response_service)
    handlers = {TASK_TYPE: cleanup_handler()}
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            status.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers=handlers,
        )
    assert cancel(status.request_id).state == "cleanup_queued"
    assert TaskRun.objects.get(pk=status.task_id).state == "running"
    if crashed:
        task = task_status(TaskRun.objects.get(pk=status.task_id))
        expire(task_act(task, "heartbeat", lease_seconds=1))
        with task_login(ServiceRole.WORKER, exact=True):
            assert recover_hint(
                status.task_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers=handlers,
            )
    else:
        with (
            task_login(ServiceRole.WORKER, exact=True, reconnect=True),
            maintain_execution(execution),
        ):
            execution.handler.execute(execution)
    assert assert_cancelled(status).processed_count == 0


def test_cancel_between_batches_then_new_cleanup_finishes_remaining_data(
    response_service, monkeypatch
):
    """The next cleanup also removes abandoned private inventory membership."""
    status = queued(response_service)
    original_batch = cleanup_tasks.apply_checkpoint
    original_progress = Execution.progress

    def small_batch(request_id, claim):
        """Ensure this test has more than one independently committed batch."""
        return original_batch(request_id, claim, maximum=2)

    def stop_after_progress(execution, current, total, **options):
        """Simulate an Admin's separate request after the batch has committed."""
        result = original_progress(execution, current, total, **options)
        cancel(status.request_id)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(cleanup_tasks, "apply_checkpoint", small_batch)
        patch.setattr(Execution, "progress", stop_after_progress)
        assert run(status)
    request = assert_cancelled(status)
    assert 0 < request.processed_count < request.inventory_total
    retained_progress = request.processed_count
    assert ProductionCleanupTarget.objects.filter(request=request).exists()
    subsequent = queued(response_service)
    assert run(subsequent)
    assert not ProductionCleanupTarget.objects.exists()
    request.refresh_from_db()
    assert request.processed_count == retained_progress and request.state == "cancelled"


@pytest.mark.parametrize(
    "boundary", ["complete", "database_failure", "exhausted_failure"]
)
def test_cancellation_at_terminal_boundary_is_not_stranded(
    response_service, monkeypatch, boundary
):
    """Honor intent recorded between an effect and terminal acknowledgment."""
    status = queued(response_service)
    if boundary == "exhausted_failure":
        task = task_status(TaskRun.objects.get(pk=status.task_id))
        for _ in range(4):
            task = task_act(task, "claim", lease_seconds=60)
            task = task_act(task, "retryable_failure")
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_sleep(1.05)")
    original_effect = Execution.effect
    cancelled = False
    failed = False

    @contextmanager
    def effect(execution):
        """Insert Admin intent only after the work transaction is released."""
        nonlocal cancelled
        try:
            with original_effect(execution):
                yield
        finally:
            request = ProductionTransitionRequest.objects.get(pk=status.request_id)
            if not cancelled and (request.state == "cleanup_complete" or failed):
                cancelled = True
                cancel(status.request_id)

    def broken(*args, **kwargs):
        """Expose the retry boundary without storing sensitive exception text."""
        nonlocal failed
        failed = True
        raise DatabaseError("synthetic private batch failure")

    monkeypatch.setattr(Execution, "effect", effect)
    if boundary != "complete":
        monkeypatch.setattr(cleanup_tasks, "apply_checkpoint", broken)
    assert run(status)
    assert cancelled
    request = assert_cancelled(status)
    assert request.processed_count == (
        request.inventory_total if boundary == "complete" else 0
    )


def test_cancellation_replay_finishes_a_now_terminal_task(response_service):
    """The same admitted intent may finish a task stopped by another owner."""
    from .test_cleanup_batches_postgresql import running_request

    status = running_request(response_service)
    cancel(status.request_id)
    intent = ProductionCleanupCancellation.objects.get(request_id=status.request_id)
    task = task_status(TaskRun.objects.get(pk=status.task_id))
    task_act(task, "permanent_failure")
    options = dict(
        request_id=status.request_id,
        command_id=intent.command_id,
        expected_version=intent.expected_version,
        actor_id=intent.actor_id,
        correlation_id=intent.correlation_id,
        admit=lambda *args: True,
    )
    assert request_cancellation(**options).state == "cancelled"
    assert request_cancellation(**options).state == "cancelled"
    assert not CampaignCredentialState.objects.get(
        campaign_id=status.campaign_id
    ).go_live_gate
