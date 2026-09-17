"""The checkpoint command performs bounded SQL-owned deletion atomically."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.campaigns.cleanup_manifest import seal_manifest
from parishkit.stewardship.campaigns.production_models import (
    ProductionCleanupCheckpoint,
    ProductionCleanupTarget,
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.delivery_states import DeliveryAction
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.outbox_storage import create_message
from parishkit.stewardship.jobs.outbox_validation import DeliveryIdentity

from ..test_outbox_validation import rendering
from .test_cleanup_inventory_postgresql import settle_test_receipts, start_request
from .test_outbox_postgresql import change
from .test_production_journal_postgresql import start
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)


def running_request(harness):
    """Admit a real manifest then bind a synthetic maintained cleanup claim."""
    with work_transaction():
        settle_test_receipts(harness)
        status = start_request(harness)
        seal_manifest(status.request_id)
    return start(status)


def delete_batch(status, *, maximum=2, command_id=None):
    """Empty command payloads are replaced by the private SQL planner's evidence."""
    with work_transaction():
        request = ProductionTransitionRequest.objects.get(pk=status.request_id)
        checkpoint = ProductionCleanupCheckpoint.objects.create(
            request=request,
            command_id=command_id or uuid4(),
            sequence=request.checkpoint_sequence + 1,
            counts={},
            deleted_count=maximum,
            batch_digest="0" * 64,
            run_id=request.run_id,
            task_fence=request.task_fence,
            worker_id=request.worker_id,
            actor_id=request.worker_id,
            correlation_id=request.run.correlation_id,
        )
        checkpoint.refresh_from_db()
        return checkpoint


@pytest.mark.parametrize("populated", [False, True])
def test_rehearsal_inventory_deletes_in_small_exact_batches(
    response_service, populated
):
    """Paired sessions and credential dependents fit finite, restartable commands."""
    if populated:
        form, answers = form_and_answers(response_service)
        answers["members"]["3"]["first_name"] = "Disposable answer"
        submit(response_service, form, answers)
    status = running_request(response_service)
    total = status.inventory_total
    observed = []
    for _ in range(total + 1):
        if not ProductionCleanupTarget.objects.filter(
            request_id=status.request_id
        ).exists():
            break
        checkpoint = delete_batch(status)
        assert 0 <= checkpoint.deleted_count <= 2
        assert 1 <= checkpoint.scanned_count <= 2
        observed.append(checkpoint.deleted_count)
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM stewardship_cleanup_effect")
            assert cursor.fetchone() == (0,)
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    assert not ProductionCleanupTarget.objects.filter(request=request).exists()
    assert sum(observed) == request.processed_count == total
    assert request.state == "cleanup_running"
    assert request.checkpoint_sequence == len(observed)


def make_testing_mail(harness):
    """Terminal synthetic mail contains no provider calls or real credentials."""
    campaign = harness.campaign
    message = create_message(
        identity=DeliveryIdentity(
            scope_id=campaign.pk,
            campaign_id=campaign.pk,
            semantic_key=uuid4(),
            mode="testing",
            routing="testing_override",
            purpose="weekly_digest",
        ),
        render=rendering(
            configuration_id=campaign.active_configuration.configuration_id
        ),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        command_id=uuid4(),
        admit=lambda *args: True,
    )
    return change(message, DeliveryAction.CANCEL_UNSENT)


def test_cancelled_testing_mail_deletes_render_cycle_and_history(response_service):
    """Only the checkpoint owner can remove the immutable terminal mail unit."""
    message = make_testing_mail(response_service)
    status = running_request(response_service)
    for _ in range(status.inventory_total + 1):
        if not ProductionCleanupTarget.objects.filter(
            request_id=status.request_id
        ).exists():
            break
        delete_batch(status)
    assert not OutboxMessage.objects.filter(pk=message.message_id).exists()
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    assert request.processed_count == status.inventory_total
    assert request.aggregate.messages == request.aggregate.cancelled == 1


def test_interrupted_batch_rolls_back_detail_membership_and_checkpoint(
    response_service,
):
    """An error after SQL deletion cannot commit any partial progress."""
    status = running_request(response_service)
    before = set(ProductionCleanupTarget.objects.values_list("pk", flat=True))
    with (
        pytest.raises(RuntimeError, match="simulated interruption"),
        transaction.atomic(),
    ):
        delete_batch(status)
        raise RuntimeError("simulated interruption")
    assert set(ProductionCleanupTarget.objects.values_list("pk", flat=True)) == before
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    assert request.processed_count == request.checkpoint_sequence == 0
    assert not request.checkpoints.exists()
    assert delete_batch(status).deleted_count > 0


@pytest.mark.parametrize("maximum", [0, 1, 1001])
def test_checkpoint_cannot_choose_unbounded_or_incapable_budget(
    response_service, maximum
):
    """The command must allow coupled units and remain within the fixed limit."""
    status = running_request(response_service)
    with pytest.raises(IntegrityError, match="current command ownership"):
        delete_batch(status, maximum=maximum)
    assert not ProductionCleanupCheckpoint.objects.exists()
