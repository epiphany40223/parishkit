"""Stale source hints cancel only with concrete supersession and drain evidence."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from django.db import connections

from parishkit.stewardship.audit.models import AuditContext, AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.dispatch import Handler, WorkQueue, recover_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.scanning import collect_hints
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.leases import release_source, reserve_source_request
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.outcomes import admit_refresh_metadata, recovery_plan
from parishkit.stewardship.source.requests import TASK_TYPE
from parishkit.stewardship.source.snapshots import promote_snapshot
from parishkit.stewardship.source.superseding import cancel_superseded_refresh

from .campaign_builders import add_draft, initialized
from .test_source_attempts_postgresql import setup, stage
from .test_source_leases_postgresql import delay
from .test_source_outcomes_postgresql import abandon
from .test_source_requests_postgresql import command
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Disposable flush recreates only idle source migration seeds."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def queued(tmp_path, *, supersede=True):
    """An actual queued request becomes obsolete when the sole campaign changes."""
    store, version, actor = initialized(tmp_path)
    receipt = command()
    if supersede:
        add_draft(store, version, actor)
    return receipt.task_root_id


def registry():
    """Only metadata/recovery is exercised; this fixture never executes fresh reads."""
    return {
        TASK_TYPE: Handler(
            WorkQueue.GENERAL,
            admit_refresh_metadata,
            lambda execution: None,
            recover=recovery_plan,
            scope=work_transaction,
        )
    }


def recover(execution):
    """Run the generic recovery dispatcher with the concrete source metadata owner."""
    return recover_hint(
        execution.claim.run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers=registry(),
    )


def test_waiting_old_window_cancels_once_with_only_digest_audit(tmp_path):
    """An old command is retained as cancelled, not rebound to the new campaign."""
    identifier = queued(tmp_path)
    original = TaskRun.objects.get(pk=identifier)
    worker = uuid4()
    assert cancel_superseded_refresh(identifier, worker_id=worker)
    assert not cancel_superseded_refresh(identifier, worker_id=worker)
    task = TaskRun.objects.get(pk=identifier)
    assert (
        task.state == "cancelled"
        and task.domain_request_id == original.domain_request_id
    )
    event = AuditEvent.objects.get(event_type="source_superseded")
    assert event.actor_id == worker and event.subject_id == identifier
    context = AuditContext.objects.get(event=event).context
    assert set(context) == {"source_fingerprint", "candidate_fingerprint", "outcome"}
    assert context["outcome"] == "cancelled"
    assert context["source_fingerprint"] != context["candidate_fingerprint"]


def test_current_window_is_not_cancelled(tmp_path):
    """A waiting current request remains available to the real source consumer."""
    identifier = queued(tmp_path, supersede=False)
    assert not cancel_superseded_refresh(identifier, worker_id=uuid4())
    assert TaskRun.objects.get(pk=identifier).state == "queued"
    assert not AuditEvent.objects.filter(event_type="source_superseded").exists()
    hints, _ = collect_hints(handlers=registry())
    assert [hint.run_id for hint in hints] == [identifier]


def test_running_owner_is_not_impersonated_by_cancellation(tmp_path):
    """Even stale scope must not let another process forge a live worker outcome."""
    _, execution, _, store, version, actor = setup(tmp_path)
    add_draft(store, version, actor)
    assert not cancel_superseded_refresh(execution.claim.run_id, worker_id=uuid4())
    assert TaskRun.objects.get(pk=execution.claim.run_id).state == "running"


def test_dispatcher_can_fence_and_cancel_expired_old_scope(tmp_path):
    """Fresh ordinary admission denial cannot permanently prevent lease recovery."""
    _, execution, lease, store, version, actor = setup(tmp_path)
    release_source(lease)
    execution.heartbeat(seconds=1)
    add_draft(store, version, actor)
    delay(1.05)
    hints, _ = collect_hints(handlers=registry())
    assert [hint.run_id for hint in hints] == [execution.claim.run_id]
    assert recover(execution)
    row = TaskRun.objects.get(pk=execution.claim.run_id)
    assert row.state == "cancelled" and row.action == "recovery_cancel"


def test_cancellation_waits_for_old_attempts_external_deadline(tmp_path):
    """New scope does not erase a potentially undrained old provider read."""
    credential, execution, lease, store, version, actor = setup(tmp_path)
    begin_refresh_attempt(execution, lease, credential)
    with execution.effect():
        reserve_source_request(lease, timeout_seconds=2, safety_seconds=1)
        release_source(lease)
    abandon(execution)
    add_draft(store, version, actor)
    identifier = execution.claim.run_id
    assert not cancel_superseded_refresh(identifier, worker_id=uuid4())
    delay(2.05)
    assert cancel_superseded_refresh(identifier, worker_id=uuid4())
    assert TaskRun.objects.get(pk=identifier).state == "cancelled"


def test_completed_observation_is_acknowledged_instead_of_cancelled(tmp_path):
    """Completion proof takes precedence over a later configuration change."""
    credential, execution, lease, store, version, actor = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, lease, credential)
    stage(attempt, execution, lease)
    with execution.effect():
        promote_snapshot(attempt.snapshot_id, lease, admit=permit, reconcile=permit)
    abandon(execution)
    add_draft(store, version, actor)
    assert not cancel_superseded_refresh(execution.claim.run_id, worker_id=uuid4())
    assert recover(execution)
    assert TaskRun.objects.get(pk=execution.claim.run_id).state == "succeeded"


def test_failed_audit_rolls_back_cancellation(tmp_path, monkeypatch):
    """The observable transition and its original/replacement scope proof are atomic."""
    from parishkit.stewardship.source import superseding

    identifier = queued(tmp_path)

    def fail(*args, **kwargs):
        """Inject a durable audit failure, not a provider or authorization bypass."""
        raise RuntimeError("Synthetic audit failure")

    monkeypatch.setattr(superseding, "record_action", fail)
    with pytest.raises(RuntimeError, match="audit failure"):
        cancel_superseded_refresh(identifier, worker_id=uuid4())
    assert TaskRun.objects.get(pk=identifier).state == "queued"


def test_concurrent_sweepers_create_one_cancellation(tmp_path):
    """The retry-root and work order serialize independent scheduler observations."""
    identifier = queued(tmp_path)

    def cancel(_):
        """Each test process has independent SQL ownership and closes its connection."""
        try:
            return cancel_superseded_refresh(identifier, worker_id=uuid4())
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(cancel, range(2))) == [False, True]
    assert AuditEvent.objects.filter(event_type="source_superseded").count() == 1
