"""Incomplete deltas retain one real full-refresh dependency and their provenance."""

from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.dispatch import WorkQueue, claim_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.scanning import collect_hints
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.fallback import request_full_fallback
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.outcomes import completed_snapshot, fallback_state
from parishkit.stewardship.source.refresh_models import (
    SourceRefreshCommand,
    SourceRefreshFallback,
    SourceRefreshRequest,
)
from parishkit.stewardship.source.rejection import reject_snapshot
from parishkit.stewardship.source.requests import TASK_TYPE
from parishkit.stewardship.source.snapshots import promote_snapshot
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import add_draft
from .test_source_attempts_postgresql import configured, setup, stage
from .test_source_leases_postgresql import delay
from .test_source_refreshing_postgresql import next_delta, seed_full
from .test_source_requests_postgresql import claim, command
from .test_source_snapshots_postgresql import permit
from .test_source_superseding_postgresql import registry
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Recreate only idle source seeds after the disposable database flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def unseeded(tmp_path):
    """Claim a delta before any source baseline exists, without faking an attempt."""
    configured(tmp_path)
    return claim(command(cause="delta", actor_id=None))


def seeded(tmp_path):
    """Start a concrete delta against a genuine completed synthetic full observation."""
    credential, execution, lease, *_ = setup(tmp_path)
    seed_full(credential, execution, lease)
    execution, lease = next_delta()
    attempt = begin_refresh_attempt(execution, lease, credential)
    return execution, lease, attempt


def test_no_base_fallback_creates_one_full_dependency_not_success(tmp_path):
    """The original delta and requested full remain independent nonterminal Tasks."""
    execution = unseeded(tmp_path)
    first = request_full_fallback(execution)
    second = request_full_fallback(execution)
    assert first.pk == second.pk and SourceRefreshFallback.objects.count() == 1
    first = SourceRefreshFallback.objects.select_related("command__request").get()
    assert first.reason == "no_base" and first.attempt_id is None
    assert (
        first.task_id == execution.claim.run_id
        and first.task_fence == execution.claim.fence
    )
    assert first.command.cause == "fallback" and first.command.request.kind == "full"
    assert first.command.request_id != first.request_id
    assert TaskRun.objects.get(pk=first.task_id).state == "running"
    assert first.command.request.task_root.state == "queued"
    assert SourceCurrent.objects.get().snapshot_id is None
    assert TaskRun.objects.count() == SourceRefreshRequest.objects.count() == 2


def test_full_fallback_uses_normal_pending_manual_coalescing(tmp_path):
    """A waiting manual full refresh can satisfy the fallback without another load."""
    execution = unseeded(tmp_path)
    manual = command()
    result = request_full_fallback(execution)
    assert result.command.request_id == manual.request_id
    assert result.command_id != manual.command_id
    assert TaskRun.objects.count() == 2 and SourceRefreshCommand.objects.count() == 3


def test_incomplete_delta_requires_rejected_current_attempt(tmp_path):
    """The unpromoted candidate remains as evidence linked to the full dependency."""
    execution, lease, attempt = seeded(tmp_path)
    with pytest.raises(PermissionError, match="rejected"):
        request_full_fallback(execution, attempt_id=attempt.pk)
    assert not SourceRefreshFallback.objects.exists()
    with execution.effect():
        reject_snapshot(attempt.snapshot_id, lease, admit=permit)
    result = request_full_fallback(execution, attempt_id=attempt.pk)
    assert result.reason == "incomplete_delta" and result.attempt_id == attempt.pk
    assert SourceCurrent.objects.get().snapshot_id == attempt.snapshot.base_id


def test_existing_base_cannot_be_reported_as_missing(tmp_path):
    """Unavailable delta semantics must retain their concrete rejected attempt."""
    execution, _, _ = seeded(tmp_path)
    with pytest.raises(PermissionError, match="absent current"):
        request_full_fallback(execution)
    assert not SourceRefreshFallback.objects.exists()


def test_full_request_cannot_fall_back_to_another_full_request(tmp_path):
    """Only delta-to-full edges are allowed, so dependencies cannot form cycles."""
    _, execution, _, *_ = setup(tmp_path)
    with pytest.raises(PermissionError, match="Only a delta"):
        request_full_fallback(execution)
    assert not SourceRefreshFallback.objects.exists()


def test_audit_failure_rolls_back_both_dependency_and_new_full_work(
    tmp_path, monkeypatch
):
    """There is no orphan queued full refresh after failure to record fallback."""
    from parishkit.stewardship.source import fallback

    execution = unseeded(tmp_path)

    def fail(*args, **kwargs):
        """Inject a failure after durable rows are prepared but before commit."""
        raise RuntimeError("Synthetic fallback audit failure")

    monkeypatch.setattr(fallback, "record_action", fail)
    with pytest.raises(RuntimeError, match="audit failure"):
        request_full_fallback(execution)
    assert not SourceRefreshFallback.objects.exists()
    assert TaskRun.objects.count() == SourceRefreshRequest.objects.count() == 1
    assert SourceRefreshCommand.objects.count() == 1


@pytest.mark.parametrize("field,value", [("actor_id", uuid4()), ("task_fence", 99)])
def test_sql_rejects_forged_creating_worker_before_unique_check(tmp_path, field, value):
    """Even direct ORM inserts require the live exact worker and current task fence."""
    execution = unseeded(tmp_path)
    result = request_full_fallback(execution)
    values = dict(
        request_id=result.request_id,
        command_id=result.command_id,
        task_id=result.task_id,
        task_fence=result.task_fence,
        reason=result.reason,
        actor_id=result.actor_id,
    )
    values[field] = value
    with (
        pytest.raises(IntegrityError, match="current request/worker"),
        work_transaction(),
    ):
        SourceRefreshFallback.objects.create(**values)


def test_fallback_history_is_immutable_and_prevents_destructive_downgrade(tmp_path):
    """Keep the dependency guards installed when any fallback history is retained."""
    result = request_full_fallback(unseeded(tmp_path))
    with pytest.raises(StorageInvariantError):
        SourceRefreshFallback.objects.filter(pk=result.pk).update(
            reason="incomplete_delta"
        )
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_source_refresh_fallback SET reason='incomplete_delta' "
            "WHERE id=%s",
            [result.pk],
        )
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    try:
        with pytest.raises(IntegrityError, match="fallback history"):
            executor.migrate([("stewardship_source", "0012_refresh_attempt_guards")])
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regprocedure('stewardship_refresh_fallback_guard_v1()')"
            )
            assert cursor.fetchone()[0] is not None
    finally:
        MigrationExecutor(connection).migrate(targets)


def waiting(execution):
    """Park the original worker through its concrete metadata admission."""
    execution = replace(execution, handler=registry()[TASK_TYPE])
    result = request_full_fallback(execution)
    execution.transition("retryable_failure", retry_seconds=1)
    delay(1.05)
    return result


def claim_registered(run_id):
    """Use the real source claim/hint admission without executing the fixture body."""
    return claim_hint(
        run_id, queue=WorkQueue.GENERAL, worker_id=uuid4(), handlers=registry()
    )


def test_pending_fallback_suppresses_hints_and_does_not_consume_attempts(tmp_path):
    """Waiting for a lengthy full scan does not repeatedly claim or rerun the delta."""
    execution = unseeded(tmp_path)
    dependency = waiting(execution)
    for _ in range(3):
        hints, _ = collect_hints(handlers=registry())
        assert [hint.run_id for hint in hints] == [
            dependency.command.request.task_root_id
        ]
    with pytest.raises(PermissionError, match="waiting"):
        claim_registered(execution.claim.run_id)
    row = TaskRun.objects.get(pk=execution.claim.run_id)
    assert row.state == "retry_wait" and row.attempt == 1


def test_full_dependency_completion_satisfies_delta_after_campaign_changes(tmp_path):
    """Explicit full provenance can fulfill its delta; new reads remain denied."""
    credential, store, version, actor = configured(tmp_path)
    parent = claim(command(cause="delta", actor_id=None))
    dependency = waiting(parent)
    target = claim_registered(dependency.command.request.task_root_id)
    with target.effect():
        lease = acquire_source(
            task_id=target.claim.run_id,
            task_fence=target.claim.fence,
            worker_id=target.claim.worker_id,
            phase="full",
        )
    attempt = begin_refresh_attempt(target, lease, credential)
    stage(attempt, target, lease)
    with target.effect():
        promote_snapshot(attempt.snapshot_id, lease, admit=permit, reconcile=permit)
        release_source(lease)
    target.transition("complete")
    add_draft(store, version, actor)
    resumed = claim_registered(parent.claim.run_id)
    with work_transaction():
        status = _status(TaskRun.objects.get(pk=resumed.claim.run_id))
        assert fallback_state(status) == "complete"
        assert completed_snapshot(status) == attempt.snapshot_id
    with pytest.raises(PermissionError), resumed.effect():
        pytest.fail("Completed historical work must not gain new-work permission")
    resumed.transition("complete")
    assert TaskRun.objects.get(pk=parent.claim.run_id).state == "succeeded"
    assert SourceRefreshFallback.objects.get().pk == dependency.pk


@pytest.mark.parametrize(
    "action,outcome", [("permanent_failure", "failed"), ("safe_cancel", "cancelled")]
)
def test_dependency_failure_or_cancellation_is_not_success(tmp_path, action, outcome):
    """A full dependency's negative terminal result propagates without a new scan."""
    parent = unseeded(tmp_path)
    dependency = waiting(parent)
    target = claim_registered(dependency.command.request.task_root_id)
    # This fixture supplies a definitive target-domain failure/cancellation;
    # the behavior under test is the parent's real dependency admission.
    with work_transaction():
        act(_status(TaskRun.objects.get(pk=target.claim.run_id)), action)
    resumed = claim_registered(parent.claim.run_id)
    with work_transaction():
        status = _status(TaskRun.objects.get(pk=resumed.claim.run_id))
        assert completed_snapshot(status) is None and fallback_state(status) == outcome
    resumed.transition(action)
    assert TaskRun.objects.get(pk=parent.claim.run_id).state == outcome


def test_target_task_success_without_promoted_proof_is_rejected(tmp_path):
    """A generic Task success cannot fabricate completion of the full observation."""
    parent = unseeded(tmp_path)
    dependency = waiting(parent)
    target = claim_registered(dependency.command.request.task_root_id)
    with work_transaction():
        act(_status(TaskRun.objects.get(pk=target.claim.run_id)), "complete")
        with pytest.raises(StorageInvariantError, match="verified dependency"):
            fallback_state(_status(TaskRun.objects.get(pk=parent.claim.run_id)))
