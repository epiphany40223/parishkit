"""Atomic capture and replay keep readiness ownership outside the cleanup worker."""

from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns import cleanup_requests
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)

from .test_cleanup_tasks_postgresql import queued, run

pytestmark = pytest.mark.django_db(transaction=True)


def original_intent(request):
    """Reconstruct the synthetic Admin's original acknowledged request."""
    return dict(
        campaign_id=request.campaign_id,
        request_key=request.request_key,
        actor_id=request.initiated_by_id,
        correlation_id=uuid4(),
        readiness_digest=request.aggregate.readiness_digest,
        acknowledged_at=request.acknowledged_at,
        reauthenticated_at=request.reauthenticated_at,
        admit=lambda *args: True,
    )


def test_begin_replay_after_deletion_uses_original_evidence(response_service):
    """Changed current row counts cannot invalidate the original idempotency key."""
    status = queued(response_service)
    intent = original_intent(
        ProductionTransitionRequest.objects.get(pk=status.request_id)
    )
    assert run(status)
    replay = cleanup_requests.begin_cleanup(**intent)
    assert replay.request_id == status.request_id and replay.state == "cleanup_complete"
    assert replay.inventory_total == status.inventory_total
    assert ProductionTransitionRequest.objects.count() == 1
    with pytest.raises(PermissionError):
        cleanup_requests.begin_cleanup(**(intent | {"admit": lambda *args: False}))
    with pytest.raises(ValueError, match="already bound"):
        cleanup_requests.begin_cleanup(**(intent | {"readiness_digest": "a" * 64}))


def test_failed_sealing_rolls_back_gate_epoch_and_task(response_service, monkeypatch):
    """The request is never published to a worker without its entire manifest."""
    before = CampaignCredentialState.objects.get(campaign=response_service.campaign)

    def fail(*args):
        """Fail at the last step of the composed capture transaction."""
        raise RuntimeError("synthetic seal interruption")

    monkeypatch.setattr(cleanup_requests, "seal_manifest", fail)
    with pytest.raises(RuntimeError, match="synthetic seal interruption"):
        queued(response_service)
    after = CampaignCredentialState.objects.get(pk=before.pk)
    assert (after.version, after.go_live_gate, after.rehearsal_epoch_id) == (
        before.version,
        before.go_live_gate,
        before.rehearsal_epoch_id,
    )
    assert not ProductionTransitionRequest.objects.exists()
    from parishkit.stewardship.jobs.models import TaskRun

    assert not TaskRun.objects.filter(task_type="production_cleanup").exists()


def test_external_restore_history_rejects_capture_before_gate_commit(response_service):
    """Do not start deletion when another retained workflow owns a reference."""
    from datetime import timedelta

    from django.db import IntegrityError

    from parishkit.stewardship.campaigns.models import (
        RestoreDeliveryHold,
        ScheduleDefinition,
    )
    from parishkit.stewardship.campaigns.resolutions import resolve_restore_hold

    from .campaign_builders import (
        admit_test_work,
        occurrence,
        restored_runtime,
    )

    actor = uuid4()
    definition = ScheduleDefinition.objects.filter(
        campaign=response_service.campaign
    ).first()
    row = occurrence(definition, actor)
    start = response_service.campaign.active_configuration.starts_at
    with restored_runtime(start) as restore_id:
        hold = RestoreDeliveryHold.objects.create(
            restore_id=restore_id,
            definition=definition,
            mode="testing",
            target=row.target,
            slot=row.slot,
            backup_at=start,
            window_start=start,
            window_end=start + timedelta(days=1),
            discovery="synthetic restore inventory",
            actor_id=actor,
            correlation_id=uuid4(),
        )
        resolve_restore_hold(
            hold_id=hold.pk,
            expected_version=hold.version,
            state="resend_authorized",
            evidence="synthetic retained restore decision",
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
            recovery_occurrence_id=row.pk,
        )
    before = CampaignCredentialState.objects.get(campaign=response_service.campaign)
    with pytest.raises(IntegrityError, match="external workflow references"):
        queued(response_service)
    after = CampaignCredentialState.objects.get(pk=before.pk)
    assert (after.go_live_gate, after.rehearsal_epoch_id, after.version) == (
        before.go_live_gate,
        before.rehearsal_epoch_id,
        before.version,
    )
    assert not ProductionTransitionRequest.objects.exists()
    assert RestoreDeliveryHold.objects.get(pk=hold.pk).recovery_occurrence_id == row.pk


@pytest.mark.parametrize("routing", ["production", "operational"])
def test_external_outbox_reference_rejects_capture(response_service, routing):
    """A retained mixed-route outbox cannot strand an inventoried occurrence."""
    from django.db import IntegrityError
    from django.db.models import F

    from parishkit.stewardship.campaigns.models import (
        ScheduleDefinition,
        ScheduleOccurrence,
    )
    from parishkit.stewardship.jobs.delivery_states import DeliveryAction

    from .campaign_builders import occurrence
    from .test_cleanup_inventory_postgresql import mixed_mail
    from .test_outbox_postgresql import change

    messages = mixed_mail(response_service)
    change(messages["testing_override"], DeliveryAction.CANCEL_UNSENT)
    # Keep the terminal occurrence consistent with its linked message so this
    # regression reaches cleanup's independent external-owner check.
    change(messages[routing], DeliveryAction.CANCEL_UNSENT)
    row = occurrence(ScheduleDefinition.objects.first(), uuid4())
    # The occurrence's UUID-only outbox pointer admits this state through all
    # normal SQL guards. Capture must reject it, not delete its external owner.
    ScheduleOccurrence.objects.filter(pk=row.pk).update(
        state="skipped",
        reason="synthetic_skip",
        outbox_id=messages[routing].message_id,
        version=F("version") + 1,
    )
    before = CampaignCredentialState.objects.get(campaign=response_service.campaign)
    with pytest.raises(IntegrityError, match="external workflow references"):
        queued(response_service)
    after = CampaignCredentialState.objects.get(pk=before.pk)
    assert (after.go_live_gate, after.rehearsal_epoch_id, after.version) == (
        before.go_live_gate,
        before.rehearsal_epoch_id,
        before.version,
    )
    assert not ProductionTransitionRequest.objects.exists()


@pytest.mark.parametrize("gate_state", ["tombstone", "preparing", "running"])
def test_historical_gate_distinguishes_completed_from_active_purge(
    response_service, gate_state
):
    """A future-owner sentinel cannot turn completed purge into a permanent hold."""
    from django.db import IntegrityError, connection, transaction

    from parishkit.stewardship.campaigns.models import Campaign, CampaignWorkGate
    from parishkit.stewardship.deployment import ServiceRole
    from parishkit.stewardship.storage import StorageInvariantError

    from .test_background_grants_postgresql import task_login

    # Like the control-journal fixtures, load only future purge sentinel states
    # as the disposable schema owner. BG-11 is not implemented here. Every
    # cleanup operation below executes with all application guards enabled.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("ALTER TABLE stewardship_campaign DISABLE TRIGGER USER")
        cursor.execute(
            "ALTER TABLE stewardship_campaign_work_gate DISABLE TRIGGER USER"
        )
        historical = Campaign.objects.create(
            state="purged" if gate_state == "tombstone" else "archived",
            active_configuration=response_service.campaign.active_configuration,
        )
        gate = CampaignWorkGate.objects.create(
            campaign=historical,
            request_id=uuid4(),
            initiated_by_id=uuid4(),
            state=gate_state,
        )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE stewardship_campaign ENABLE TRIGGER USER")
        cursor.execute("ALTER TABLE stewardship_campaign_work_gate ENABLE TRIGGER USER")
    if gate_state == "tombstone":
        status = queued(response_service)
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            assert run(status)
        assert (
            ProductionTransitionRequest.objects.get(pk=status.request_id).state
            == "cleanup_complete"
        )
    else:
        with pytest.raises((IntegrityError, StorageInvariantError)):
            queued(response_service)
        assert not ProductionTransitionRequest.objects.exists()
    assert CampaignWorkGate.objects.get(pk=gate.pk).state == gate_state
