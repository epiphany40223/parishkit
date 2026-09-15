"""Real competing connections prove Testing cleanup's shared admission order."""

from concurrent.futures import ThreadPoolExecutor
from queue import Queue

import pytest

from parishkit.stewardship.campaigns.cleanup_batches import apply_checkpoint
from parishkit.stewardship.campaigns.production_models import (
    ProductionCleanupCheckpoint,
    ProductionCleanupTarget,
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.responses.baselines import FamilyAdmissionDenied
from parishkit.stewardship.responses.models import Submission

from .test_cleanup_batches_postgresql import running_request
from .test_cleanup_cancellation_postgresql import cancel
from .test_cleanup_tasks_postgresql import queued, run
from .test_response_concurrency_postgresql import contender, wait_for_work_lock
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("first_owner", ["cleanup", "submission"])
def test_gate_and_submission_have_one_serialized_winner(response_service, first_owner):
    """An earlier response is inventoried; a later response cannot commit."""
    form, answers = form_and_answers(response_service)
    ready = Queue()

    def submit_now():
        """Use the actual browser's original form and ordinary Submit admission."""
        try:
            return submit(response_service, form, answers).submission
        except FamilyAdmissionDenied:
            return None

    def cleanup_now():
        """Acquire the gate, invalidate the epoch and seal the real inventory."""
        return queued(response_service)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with work_transaction():
            future = pool.submit(
                contender,
                ready,
                submit_now if first_owner == "cleanup" else cleanup_now,
            )
            wait_for_work_lock(ready.get(timeout=5))
            first = cleanup_now() if first_owner == "cleanup" else submit_now()
            assert not future.done()
        later = future.result(timeout=10)
    status = first if first_owner == "cleanup" else later
    response = later if first_owner == "cleanup" else first
    assert Submission.objects.count() == (0 if first_owner == "cleanup" else 1)
    if response is not None:
        assert ProductionCleanupTarget.objects.filter(
            request_id=status.request_id, category="submissions", target_id=response.pk
        ).exists()
    assert run(status)
    assert not Submission.objects.exists()


def test_cancellation_waits_for_committed_batch(response_service):
    """An Admin cannot split a batch; once intent wins no next batch is admitted."""
    from django.db import IntegrityError

    status = running_request(response_service)
    claim = TaskClaim(status.run_id, status.task_fence, status.worker_id)
    ready = Queue()

    def cancel_current():
        """Refresh the reviewed version after acquiring the common work order."""
        with work_transaction():
            return cancel(status.request_id)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with work_transaction():
            future = pool.submit(contender, ready, cancel_current)
            wait_for_work_lock(ready.get(timeout=5))
            applied = apply_checkpoint(status.request_id, claim, maximum=2)
            assert not future.done()
        future.result(timeout=10)
    request = ProductionTransitionRequest.objects.get(pk=status.request_id)
    assert request.processed_count == applied.processed_count > 0
    assert ProductionCleanupCheckpoint.objects.count() == 1
    with pytest.raises(IntegrityError), work_transaction():
        apply_checkpoint(status.request_id, claim, maximum=2)
    request.refresh_from_db()
    assert request.processed_count == applied.processed_count


def test_waiting_old_claim_cannot_delete_after_recovery(response_service):
    """A real blocked contender rechecks its fence after a new owner takes over."""
    from django.db import connection

    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.ownership import TaskOwnershipLost
    from parishkit.stewardship.jobs.storage import _status

    from .test_taskrun_postgresql import act as task_act
    from .test_taskrun_postgresql import expire

    status = running_request(response_service)
    old_claim = TaskClaim(status.run_id, status.task_fence, status.worker_id)
    task = _status(TaskRun.objects.get(pk=status.task_id))
    task = expire(task_act(task, "heartbeat", lease_seconds=1))
    task = task_act(task, "recovery_retry", retry_seconds=1)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(1.05)")
    ready = Queue()

    def stale_delete():
        """Run the old command only after it gets the common work lock."""
        with work_transaction():
            return apply_checkpoint(status.request_id, old_claim)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with work_transaction():
            future = pool.submit(contender, ready, stale_delete)
            wait_for_work_lock(ready.get(timeout=5))
            replacement = task_act(task, "claim", lease_seconds=60)
            assert replacement.fence > old_claim.fence
        with pytest.raises(TaskOwnershipLost):
            future.result(timeout=10)
    assert not ProductionCleanupCheckpoint.objects.exists()
    assert ProductionCleanupTarget.objects.count() == status.inventory_total


def test_lease_expiry_inside_delete_rolls_back_entire_batch(response_service):
    """A slow actual SQL delete cannot commit under a lease that expired mid-batch."""
    from django.db import IntegrityError, connection

    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status

    from .test_taskrun_postgresql import act as task_act

    status = running_request(response_service)
    task = _status(TaskRun.objects.get(pk=status.task_id))
    claim = TaskClaim(status.run_id, status.task_fence, status.worker_id)
    # This temporary test-only trigger delays a genuine deletion. It never
    # disables fencing, skips domain checks or changes the production schema.
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE FUNCTION pg_temp.cleanup_test_delay() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(1.1); RETURN OLD; END $$"
        )
        cursor.execute(
            "CREATE TRIGGER cleanup_test_delay AFTER DELETE "
            "ON stewardship_rehearsal_code_mac FOR EACH ROW "
            "EXECUTE FUNCTION pg_temp.cleanup_test_delay()"
        )
    try:
        task_act(task, "heartbeat", lease_seconds=1)
        with pytest.raises(IntegrityError), work_transaction():
            apply_checkpoint(status.request_id, claim)
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "DROP TRIGGER cleanup_test_delay ON stewardship_rehearsal_code_mac"
            )
    assert not ProductionCleanupCheckpoint.objects.exists()
    assert ProductionCleanupTarget.objects.count() == status.inventory_total
