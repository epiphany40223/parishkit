"""Refresh recovery uses durable completion and finite read-drain evidence."""

from dataclasses import replace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.source.attempts import begin_refresh_attempt
from parishkit.stewardship.source.leases import (
    acquire_source,
    release_source,
    reserve_source_request,
)
from parishkit.stewardship.source.models import SourceCurrent, SourceMutationLease
from parishkit.stewardship.source.outcomes import (
    admit_refresh_metadata,
    completed_snapshot,
    read_attempts_drained,
    recovery_plan,
)
from parishkit.stewardship.source.rejection import reject_snapshot
from parishkit.stewardship.source.snapshots import promote_snapshot
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .campaign_builders import add_draft, restored_runtime
from .source_builders import running_source_task
from .test_source_attempts_postgresql import setup, stage
from .test_source_leases_postgresql import delay
from .test_source_requests_postgresql import claim as claim_request
from .test_source_requests_postgresql import command
from .test_source_snapshots_postgresql import permit
from .test_taskrun_postgresql import act, expire

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def source_singletons():
    """Recreate only idle migration seeds after the disposable database flush."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    SourceCurrent.objects.get_or_create(singleton=True)


def status(execution):
    """Freeze fresh actual Task state for every ownership-sensitive proof."""
    return _status(TaskRun.objects.get(pk=execution.claim.run_id))


def abandon(execution):
    """Use genuine short lease expiry, not disabled guards or fabricated time."""
    execution.heartbeat(seconds=1)
    with work_transaction():
        return expire(status(execution))


@pytest.mark.parametrize("state", ["staging", "ready", "rejected", "promoted"])
def test_only_bound_promoted_manifest_proves_completion(tmp_path, state):
    """Finishing validation or recording rejection is never Task success."""
    credential, execution, lease, *_ = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, lease, credential)
    if state in {"ready", "promoted"}:
        stage(attempt, execution, lease)
    with execution.effect():
        if state == "promoted":
            promote_snapshot(attempt.snapshot_id, lease, admit=permit, reconcile=permit)
        elif state == "rejected":
            reject_snapshot(attempt.snapshot_id, lease, admit=permit)
    with work_transaction():
        assert completed_snapshot(status(execution)) == (
            attempt.snapshot_id if state == "promoted" else None
        )


def test_recovery_acknowledges_promotion_despite_a_later_campaign_change(tmp_path):
    """Post-promotion crash recovery must neither lose completion nor rescan."""
    credential, execution, lease, store, version, actor = setup(tmp_path)
    attempt = begin_refresh_attempt(execution, lease, credential)
    stage(attempt, execution, lease)
    with execution.effect():
        promote_snapshot(attempt.snapshot_id, lease, admit=permit, reconcile=permit)
    abandoned = abandon(execution)
    add_draft(store, version, actor)
    with work_transaction():
        assert recovery_plan(abandoned).action == "recovery_complete"


def test_completion_survives_a_new_current_snapshot_without_borrowing_its_proof(
    tmp_path,
):
    """Each immutable request retains its own completed observation identity."""
    credential, first_execution, first_lease, *_ = setup(tmp_path)
    first = begin_refresh_attempt(first_execution, first_lease, credential)
    stage(first, first_execution, first_lease)
    with first_execution.effect():
        promote_snapshot(first.snapshot_id, first_lease, admit=permit, reconcile=permit)
        release_source(first_lease)
    second_execution = claim_request(command())
    with work_transaction():
        assert completed_snapshot(status(second_execution)) is None
    with second_execution.effect():
        second_lease = acquire_source(
            task_id=second_execution.claim.run_id,
            task_fence=second_execution.claim.fence,
            worker_id=second_execution.claim.worker_id,
            phase="full",
        )
    second = begin_refresh_attempt(second_execution, second_lease, credential)
    stage(second, second_execution, second_lease)
    with second_execution.effect():
        promote_snapshot(
            second.snapshot_id, second_lease, admit=permit, reconcile=permit
        )
    with work_transaction():
        assert SourceCurrent.objects.get().snapshot_id == second.snapshot_id
        assert completed_snapshot(status(first_execution)) == first.snapshot_id
        assert completed_snapshot(status(second_execution)) == second.snapshot_id


def test_abandonment_waits_for_reserved_read_drain_deadline(tmp_path):
    """Task expiry is not proof that its last isolated provider helper drained."""
    credential, execution, lease, *_ = setup(tmp_path)
    begin_refresh_attempt(execution, lease, credential)
    with execution.effect():
        reserve_source_request(lease, timeout_seconds=2, safety_seconds=1)
        release_source(lease)
    abandoned = abandon(execution)
    with work_transaction():
        assert not read_attempts_drained(abandoned)
        assert recovery_plan(abandoned) is None
        assert not admit_refresh_metadata("recovery_hint", abandoned)
    delay(2.05)
    with work_transaction():
        assert read_attempts_drained(abandoned)
        plan = recovery_plan(abandoned)
        assert plan.action == "recovery_retry" and plan.retry_seconds == 30
        assert admit_refresh_metadata("recovery_hint", abandoned)


def test_recovery_admission_resolves_request_once_and_does_not_cache_across_calls(
    tmp_path,
):
    """Composed proofs share bounded reads, not authority across later transitions."""
    _, execution, lease, *_ = setup(tmp_path)
    release_source(lease)
    abandoned = abandon(execution)
    with work_transaction():
        with CaptureQueriesContext(connection) as queries:
            assert admit_refresh_metadata("recovery_retry", abandoned)
        reads = [q["sql"] for q in queries.captured_queries]
        # One outcome binding plus the independent new-work admission owner's
        # recheck. Composed outcome predicates must not reload it individually.
        assert sum('FROM "stewardship_source_refresh_request"' in q for q in reads) == 2
        table = connection.ops.quote_name(SourceMutationLease._meta.db_table)
        assert sum(f"FROM {table}" in q for q in reads) == 1
        act(abandoned, "recovery_retry")
        with pytest.raises(StaleRecordError):
            admit_refresh_metadata("recovery_retry", abandoned)


def test_safe_new_source_fence_proves_older_attempts_drained(tmp_path):
    """A later unrelated reader's live lease does not hold the old request forever."""
    credential, execution, lease, *_ = setup(tmp_path)
    begin_refresh_attempt(execution, lease, credential)
    release_source(lease)
    acquire_source(**running_source_task(), phase="full")
    with work_transaction():
        assert read_attempts_drained(status(execution))


def test_no_attempt_cannot_have_started_attempt_required_http(tmp_path):
    """A crash before manifest creation does not require a speculative read wait."""
    _, execution, lease, *_ = setup(tmp_path)
    release_source(lease)
    abandoned = abandon(execution)
    with work_transaction():
        assert read_attempts_drained(abandoned)
        assert recovery_plan(abandoned).action == "recovery_retry"


def test_pre_manifest_source_reservation_must_expire_before_retry(tmp_path):
    """A crash between source acquisition and manifest creation cannot spin retries."""
    _, execution, _, *_ = setup(tmp_path)
    abandoned = abandon(execution)
    with work_transaction():
        assert not read_attempts_drained(abandoned)
        assert recovery_plan(abandoned) is None


def test_denied_current_scope_is_a_hold_not_invented_cancellation(tmp_path):
    """The later superseding owner must provide explicit cancellation evidence."""
    _, execution, lease, *_ = setup(tmp_path)
    release_source(lease)
    abandoned = abandon(execution)
    with work_transaction():
        backup_at = database_now()
    with restored_runtime(backup_at), work_transaction():
        assert recovery_plan(abandoned) is None


def test_recovery_has_bounded_attempts_and_never_rewrites_old_history(tmp_path):
    """Five interrupted execution claims require an explicit future Admin retry."""
    _, execution, lease, *_ = setup(tmp_path)
    release_source(lease)
    current = status(execution)
    for _ in range(4):
        with work_transaction():
            current = act(current, "retryable_failure")
        delay(1.05)
        with work_transaction():
            current = act(current, "claim")
    with work_transaction():
        current = act(current, "heartbeat", lease_seconds=1)
        current = expire(current)
        assert current.attempt == 5
        assert recovery_plan(current).action == "recovery_fail"
    assert TaskRun.objects.get(pk=current.run_id).state == "abandoned"


def test_status_and_work_order_must_be_current(tmp_path):
    """An old version or forged retry count cannot authorize recovery decisions."""
    _, execution, _, *_ = setup(tmp_path)
    before = status(execution)
    with pytest.raises(StorageInvariantError, match="ordered transaction"):
        completed_snapshot(before)
    with work_transaction(), pytest.raises(StaleRecordError):
        completed_snapshot(replace(before, attempt=99))
    execution.progress(1, 1)
    with work_transaction(), pytest.raises(StaleRecordError):
        completed_snapshot(before)
    with work_transaction(), pytest.raises(PermissionError, match="abandoned"):
        recovery_plan(status(execution))
