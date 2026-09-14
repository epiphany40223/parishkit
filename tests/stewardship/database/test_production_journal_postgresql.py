"""Go-live journal proves atomic gate/checkpoint storage, not operational readiness."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.sessions.models import Session
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import (
    CampaignCredentialState,
    RehearsalEpoch,
)
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.production_models import (
    TestingAggregate as Aggregate,
)
from parishkit.stewardship.campaigns.production_states import ProductionAction as Action
from parishkit.stewardship.campaigns.production_storage import (
    CleanupInventory,
    TestingSummary,
    begin_transition,
    change_transition,
    checkpoint_cleanup,
)
from parishkit.stewardship.campaigns.rehearsals import release_rehearsal_gate
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import change_run, retry_failed
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .test_family_auth_postgresql import family_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def permit(*args):
    """Synthetic admission only; no actual Production or provider action is enabled."""
    return True


@pytest.fixture
def intent(family_service):  # noqa: F811
    """Current rehearsal authority and fake inventory/readiness summary."""
    now = timezone.now()
    return dict(
        campaign_id=family_service.campaign.pk,
        request_key=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
        inventory=CleanupInventory("a" * 64, {"sessions": 1}),
        summary=TestingSummary("b" * 64, 0, 0, 0, 0, 0, 0),
        acknowledged_at=now,
        reauthenticated_at=now - timedelta(seconds=1),
        admit=permit,
    )


def act(status, action, **options):
    """Preserve the latest optimistic version and attribute each independent command."""
    return change_transition(
        **(
            dict(
                request_id=status.request_id,
                action=action,
                command_id=uuid4(),
                expected_version=status.version,
                actor_id=status.worker_id or uuid4(),
                correlation_id=uuid4(),
                admit=permit,
            )
            | options
        )
    )


def start(status, run_id=None, *, action=Action.START, lease_seconds=300):
    """Claim a synthetic task and bind the cleanup request to that current fence."""
    task = TaskRun.objects.get(pk=run_id or status.task_id)
    claimed = change_run(
        run_id=task.pk,
        action="claim",
        expected_version=task.version,
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=permit,
        lease_seconds=lease_seconds,
    )
    return act(
        status,
        action,
        run_id=claimed.run_id,
        task_fence=claimed.fence,
        actor_id=claimed.worker_id,
    )


def batch(status, **options):
    """One owner-supplied checkpoint; callbacks never invoke an external provider."""
    return checkpoint_cleanup(
        **(
            dict(
                request_id=status.request_id,
                command_id=uuid4(),
                expected_version=status.version,
                actor_id=status.worker_id,
                correlation_id=uuid4(),
                batch=CleanupInventory("c" * 64, {"sessions": 1}),
                admit=permit,
                apply_batch=lambda *args: True,
            )
            | options
        )
    )


def finish_task(status, action="complete"):
    """Simulate the task framework after the domain operation has committed."""
    task = TaskRun.objects.get(pk=status.run_id)
    return change_run(
        run_id=task.pk,
        action=action,
        expected_version=task.version,
        actor_id=task.worker_id,
        fence=task.fence,
        correlation_id=uuid4(),
        admit=permit,
    )


def test_begin_invalidates_epoch_and_cancel_releases_gate_without_revival(intent):
    """Go-live intent and its irreversible invalidation survive a later cancellation."""
    scope = CampaignCredentialState.objects.get(campaign_id=intent["campaign_id"])
    epoch = scope.rehearsal_epoch_id
    status = begin_transition(**intent)
    assert begin_transition(**intent) == status
    scope.refresh_from_db()
    assert scope.go_live_gate and scope.rehearsal_epoch_id is None
    assert RehearsalEpoch.objects.get(pk=epoch).state == "invalidated"
    cancelled = act(status, Action.CANCEL)
    scope.refresh_from_db()
    assert not scope.go_live_gate and scope.rehearsal_epoch_id is None
    assert (
        cancelled.state == "cancelled"
        and RehearsalEpoch.objects.get(pk=epoch).state == "invalidated"
    )
    assert TaskRun.objects.get(pk=status.task_id).state == "cancelled"
    assert SystemConfiguration.objects.get().mode == "testing"
    assert Aggregate.objects.count() == 1
    assert set(
        AuditEvent.objects.filter(subject_id=status.request_id).values_list(
            "campaign_reference", flat=True
        )
    ) == {status.campaign_id}


@pytest.mark.parametrize("denied", ["create", "invalidate_rehearsal", "create_task"])
def test_late_initial_denial_rolls_back_all_gate_and_inventory_effects(intent, denied):
    """The gate, epoch, aggregate and task are one indivisible transaction."""
    original = CampaignCredentialState.objects.get(campaign_id=intent["campaign_id"])
    with pytest.raises(PermissionError):
        begin_transition(**(intent | {"admit": lambda action, *args: action != denied}))
    current = CampaignCredentialState.objects.get(pk=original.pk)
    assert current.version == original.version and not current.go_live_gate
    assert current.rehearsal_epoch_id == original.rehearsal_epoch_id
    assert RehearsalEpoch.objects.get(pk=current.rehearsal_epoch_id).state == "active"
    assert (
        not Aggregate.objects.exists()
        and not ProductionTransitionRequest.objects.exists()
    )
    assert not TaskRun.objects.filter(task_type="production_cleanup").exists()


def test_one_gate_owner_and_protected_release(intent):
    """Another request or direct credential edit cannot bypass gate ownership."""
    status = begin_transition(**intent)
    with pytest.raises(StorageInvariantError):
        begin_transition(**(intent | {"request_key": uuid4()}))
    with pytest.raises(IntegrityError, match="retain its go-live gate"):
        release_rehearsal_gate(campaign_id=status.campaign_id, admit=permit)
    assert CampaignCredentialState.objects.get(
        campaign_id=status.campaign_id
    ).go_live_gate


def test_cleanup_and_checkpoint_are_atomic_and_replay_never_deletes_twice(intent):
    """An owning batch deletes only its synthetic row and persists progress with it."""
    status = start(begin_transition(**intent))
    session = Session.objects.create(
        session_key=uuid4().hex, session_data="", expire_date=timezone.now()
    )
    calls = []

    def remove(current, inventory):
        """Test owner binds one known disposable row to the synthetic inventory."""
        calls.append(current.request_id)
        assert inventory.counts == {"sessions": 1}
        assert Session.objects.filter(pk=session.pk).delete()[0] == 1
        return True

    command = uuid4()
    checkpointed = batch(status, command_id=command, apply_batch=remove)
    assert not Session.objects.filter(pk=session.pk).exists()
    assert batch(status, command_id=command, apply_batch=remove) == checkpointed
    assert calls == [status.request_id] and checkpointed.processed_count == 1
    complete = act(checkpointed, Action.COMPLETE)
    assert complete.state == "cleanup_complete"
    assert CampaignCredentialState.objects.get(
        campaign_id=status.campaign_id
    ).go_live_gate
    assert SystemConfiguration.objects.get().mode == "testing"
    with pytest.raises(StorageInvariantError, match="not exposed"):
        act(complete, Action.ACTIVATE)
    finish_task(complete)
    assert act(complete, Action.CANCEL).state == "cancelled"
    assert not Session.objects.filter(pk=session.pk).exists()


@pytest.mark.parametrize("failure", ["denial", "wrong_category", "wrong_worker"])
def test_late_batch_failure_restores_deleted_rows_and_no_checkpoint(intent, failure):
    """A late proof/count/fence error rolls back owner SQL and domain progress."""
    status = start(begin_transition(**intent))
    session = Session.objects.create(
        session_key=uuid4().hex, session_data="", expire_date=timezone.now()
    )

    def remove(current, inventory):
        """Make a visible SQL effect before a deliberately failing postcondition."""
        Session.objects.filter(pk=session.pk).delete()
        return failure != "denial"

    options = dict(apply_batch=remove)
    if failure == "wrong_category":
        options["batch"] = CleanupInventory("d" * 64, {"not_in_inventory": 1})
    if failure == "wrong_worker":
        options["actor_id"] = uuid4()
    with pytest.raises((PermissionError, IntegrityError)):
        batch(status, **options)
    assert Session.objects.filter(pk=session.pk).exists()
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    assert request.processed_count == request.checkpoint_sequence == 0
    assert not request.checkpoints.exists()


def test_incomplete_cleanup_and_running_cancellation_are_rejected(intent):
    """Counting a task as running is neither completed cleanup nor safe cancellation."""
    status = start(begin_transition(**intent))
    with pytest.raises(IntegrityError, match="inventory is incomplete"):
        act(status, Action.COMPLETE)
    with pytest.raises(IntegrityError, match="safe task boundary"):
        act(status, Action.CANCEL)
    with pytest.raises(StaleRecordError):
        act(status, Action.FAIL, expected_version=1, failure_reason="synthetic_failure")


def test_exhausted_failure_keeps_gate_and_retry_preserves_checkpoints(intent):
    """Failure never silently releases Testing or discards deletion evidence."""
    status = batch(start(begin_transition(**intent)))
    failed = act(status, Action.FAIL, failure_reason="cleanup_exhausted")
    finish_task(failed, "permanent_failure")
    assert CampaignCredentialState.objects.get(
        campaign_id=failed.campaign_id
    ).go_live_gate
    with pytest.raises(IntegrityError, match="explicit task retry"):
        act(failed, Action.RETRY_FAILED)
    with transaction.atomic():
        task = retry_failed(
            run_id=failed.run_id,
            command_id=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=permit,
        )
        queued = act(failed, Action.RETRY_FAILED)
    running = start(queued, task.run_id)
    assert running.processed_count == running.checkpoint_sequence == 1
    assert act(running, Action.COMPLETE).state == "cleanup_complete"


def test_replay_requires_current_admission_and_same_inventory(intent):
    """Historical request keys do not authorize a new Admin or revised inventory."""
    status = begin_transition(**intent)
    with pytest.raises(PermissionError):
        begin_transition(**(intent | {"admit": lambda *args: False}))
    with pytest.raises(ValueError, match="already bound"):
        begin_transition(
            **(
                intent
                | {"summary": replace(intent["summary"], readiness_digest="c" * 64)}
            )
        )
    actor, command = uuid4(), uuid4()
    cancelled = act(status, Action.CANCEL, actor_id=actor, command_id=command)
    assert act(status, Action.CANCEL, actor_id=actor, command_id=command) == cancelled
    with pytest.raises(PermissionError):
        act(
            status,
            Action.CANCEL,
            actor_id=actor,
            command_id=command,
            admit=lambda *args: False,
        )
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE stewardship_production_event SET state='activated'")


@pytest.mark.parametrize("crashed", [False, True])
def test_cleanup_resumes_on_new_claim_without_losing_checkpoints(intent, crashed):
    """Explicit retry and a crash after checkpoint both retain exact progress."""
    from parishkit.stewardship.jobs.storage import _status as task_status

    from .test_taskrun_postgresql import act as task_act
    from .test_taskrun_postgresql import expire

    status = batch(
        start(begin_transition(**intent), lease_seconds=1 if crashed else 300)
    )
    task = task_status(TaskRun.objects.get(pk=status.run_id))
    if crashed:
        with pytest.raises(IntegrityError, match="newer task claim"):
            act(
                status,
                Action.RECOVER,
                run_id=status.run_id,
                task_fence=status.task_fence,
                actor_id=status.worker_id,
            )
        abandoned = expire(task)
        with pytest.raises(IntegrityError, match="safe task boundary"):
            act(status, Action.CANCEL)
        waiting_task = task_act(abandoned, "recovery_retry")
    else:
        status = act(status, Action.RETRY_LATER, failure_reason="transient_failure")
        waiting_task = task_act(task, "retryable_failure")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(1.05)")
    current = start(
        status, waiting_task.run_id, action=Action.RECOVER if crashed else Action.START
    )
    assert current.task_fence > status.task_fence
    assert current.processed_count == current.checkpoint_sequence == 1
    with pytest.raises(IntegrityError, match="current task claim"):
        act(current, Action.COMPLETE, actor_id=status.worker_id)
    assert act(current, Action.COMPLETE).state == "cleanup_complete"


def test_checkpoint_cannot_exceed_known_category_cumulative_inventory(intent):
    """Known categories cannot consume each other's remaining allowance."""
    intent["inventory"] = CleanupInventory("a" * 64, {"sessions": 1, "other": 1})
    status = batch(start(begin_transition(**intent)))
    with pytest.raises(IntegrityError, match="exceeds its inventory"):
        batch(status)
    assert (
        ProductionTransitionRequest.objects.get(pk=status.request_id).processed_count
        == 1
    )


def test_production_summary_must_match_actual_testing_delivery_totals(intent):
    """Structurally plausible caller totals cannot replace the actual journal."""
    intent["summary"] = TestingSummary("b" * 64, 0, 0, 3, 1, 1, 1)
    with pytest.raises(IntegrityError, match="exact terminal Testing"):
        begin_transition(**intent)
    assert not ProductionTransitionRequest.objects.exists()
    assert not CampaignCredentialState.objects.get(
        campaign_id=intent["campaign_id"]
    ).go_live_gate
