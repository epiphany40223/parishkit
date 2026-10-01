"""Duplicate broker delivery cannot replace durable ownership or domain admission."""

from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.jobs.dispatch import (
    Handler,
    RecoveryPlan,
    WorkQueue,
    claim_hint,
    execute_hint,
    recover_hint,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import change_run, enqueue

pytestmark = pytest.mark.django_db(transaction=True)


def queued():
    """Create one explicitly authorized synthetic domain operation."""
    return enqueue(
        task_type="dispatch_probe",
        domain_request_id=uuid4(),
        actor_id=None,
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )


def test_duplicate_hint_executes_once_outside_database_transaction():
    """The durable row, not the broker's message count, determines execution."""
    task, worker, calls = queued(), uuid4(), []

    def execute(context):
        """Synthetic bounded work with durable progress and verified completion."""
        assert not connection.in_atomic_block
        calls.append(context.claim)
        context.heartbeat()
        context.progress(1, 1)
        context.transition("complete")

    handlers = {
        "dispatch_probe": Handler(WorkQueue.GENERAL, lambda *args: True, execute)
    }
    arguments = dict(queue=WorkQueue.GENERAL, worker_id=worker, handlers=handlers)
    assert execute_hint(task.run_id, **arguments)
    assert not execute_hint(task.run_id, **arguments)
    assert len(calls) == 1 and TaskRun.objects.get(pk=task.run_id).state == "succeeded"


def test_queue_and_domain_admission_are_both_required():
    """A correct routing string never bypasses transactional domain authorization."""
    task, called = queued(), []
    handler = Handler(WorkQueue.GENERAL, lambda *args: False, called.append)
    for queue in (WorkQueue.GENERAL, WorkQueue.MAIL):
        with pytest.raises(PermissionError):
            execute_hint(
                task.run_id,
                queue=queue,
                worker_id=uuid4(),
                handlers={"dispatch_probe": handler},
            )
    assert not called and TaskRun.objects.get(pk=task.run_id).state == "queued"


def test_unexpected_worker_failure_preserves_unresolved_claim():
    """An exception says nothing about whether external effects were accepted."""
    task = queued()

    def fail(context):
        """Simulate interruption after recording resumable progress."""
        context.progress(1, 2)
        raise RuntimeError("synthetic interrupted external work")

    with pytest.raises(RuntimeError, match="synthetic interrupted"):
        execute_hint(
            task.run_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={
                "dispatch_probe": Handler(WorkQueue.GENERAL, lambda *args: True, fail)
            },
        )
    row = TaskRun.objects.get(pk=task.run_id)
    assert row.state == "running" and row.progress_current == 1


def test_revoked_completion_admission_never_marks_success():
    """Admission is repeated at the last durable effect, not cached at claim."""
    task = queued()

    def admit(action, status):
        """Synthetic domain evidence admits the claim, but not completion."""
        return action == "claim"

    handler = Handler(
        WorkQueue.GENERAL, admit, lambda context: context.transition("complete")
    )
    with pytest.raises(PermissionError):
        execute_hint(
            task.run_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"dispatch_probe": handler},
        )
    assert TaskRun.objects.get(pk=task.run_id).state == "running"


def expired_task():
    """Use a real brief database lease, without disabling ownership guards."""
    task = queued()
    change_run(
        run_id=task.run_id,
        expected_version=task.version,
        action="claim",
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: True,
        lease_seconds=1,
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(1.05)")
    return task


def test_uncertain_recovery_fences_owner_but_preserves_unresolved_work():
    """Lease expiry proves ownership loss, not successful cancellation or failure."""
    task = expired_task()
    handler = Handler(WorkQueue.GENERAL, lambda *args: True, lambda context: None)
    assert not recover_hint(
        task.run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={"dispatch_probe": handler},
    )
    row = TaskRun.objects.get(pk=task.run_id)
    assert row.state == "abandoned" and row.fence == 2


def test_verified_recovery_preserves_retry_delay_and_execution_identity():
    """A safe retry resumes the same nonterminal operation, not a duplicate root."""
    task = expired_task()
    handler = Handler(
        WorkQueue.GENERAL,
        lambda *args: True,
        lambda context: None,
        recover=lambda status: RecoveryPlan("recovery_retry", 30),
    )
    arguments = dict(
        queue=WorkQueue.GENERAL, worker_id=uuid4(), handlers={"dispatch_probe": handler}
    )
    assert recover_hint(task.run_id, **arguments)
    assert TaskRun.objects.get(pk=task.run_id).state == "retry_wait"
    assert not execute_hint(task.run_id, **arguments)
    assert TaskRun.objects.count() == 1


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("fail_callback", [False, True])
def test_post_transition_hook_is_atomic_and_not_replayed(recovery, fail_callback):
    """Callbacks observe the written state; failure rolls back state and diagnostics."""
    from parishkit.stewardship.audit.models import OperationalLog
    from parishkit.stewardship.audit.schemas import ContextKind
    from parishkit.stewardship.audit.services import operational
    from parishkit.stewardship.observability import Event

    task = expired_task() if recovery else queued()
    calls = []

    def after(action, status):
        """Only an actual journal transition may create its matching diagnostic."""
        assert connection.in_atomic_block
        assert (
            status.state == TaskRun.objects.get(pk=status.run_id).state == "succeeded"
        )
        calls.append(action)
        operational(
            Event.TASK_COMPLETED,
            schema=ContextKind.TASK,
            context={"task_id": status.run_id},
        )
        if fail_callback:
            raise RuntimeError("synthetic post-transition fault")

    handler = Handler(
        WorkQueue.GENERAL,
        lambda *args: True,
        lambda execution: execution.transition("complete"),
        recover=lambda status: RecoveryPlan("recovery_complete"),
        after_transition=after,
    )
    invoke = recover_hint if recovery else execute_hint
    options = dict(
        queue=WorkQueue.GENERAL, worker_id=uuid4(), handlers={"dispatch_probe": handler}
    )
    if fail_callback:
        with pytest.raises(RuntimeError, match="post-transition fault"):
            invoke(task.run_id, **options)
        assert TaskRun.objects.get(pk=task.run_id).state == "running"
        assert not OperationalLog.objects.exists()
    else:
        assert invoke(task.run_id, **options)
        assert not invoke(task.run_id, **options)
        assert OperationalLog.objects.filter(event="task_completed").count() == 1
        # A recovered task also records its lost lease (#293), once.
        assert OperationalLog.objects.filter(event="task_lease_lost").count() == (
            1 if recovery else 0
        )
    assert calls == ["recovery_complete" if recovery else "complete"]


def test_lost_lease_is_recorded_with_the_task_and_its_silence():
    """Lease expiry says which task went silent, and for how long (#293)."""
    from parishkit.stewardship.audit.models import OperationalLog

    task = expired_task()
    handler = Handler(
        WorkQueue.GENERAL,
        lambda *args: True,
        lambda context: None,
        recover=lambda status: RecoveryPlan("recovery_retry", 30),
    )
    options = dict(
        queue=WorkQueue.GENERAL, worker_id=uuid4(), handlers={"dispatch_probe": handler}
    )
    assert recover_hint(task.run_id, **options)
    entry = OperationalLog.objects.get()
    assert (entry.event, entry.level, entry.schema) == (
        "task_lease_lost",
        "WARNING",
        "timeout",
    )
    assert entry.context == {
        "what": "lease",
        "task_id": str(task.run_id),
        "task_type": "dispatch_probe",
        "attempt": 1,
        "limit_seconds": 1,
        "elapsed_seconds": 1,
    }


def test_final_lost_lease_records_the_failed_task():
    """When the last attempt also went silent, the failure summary says so."""
    from parishkit.stewardship.audit.models import OperationalLog

    task = expired_task()
    handler = Handler(
        WorkQueue.GENERAL,
        lambda *args: True,
        lambda context: None,
        recover=lambda status: RecoveryPlan("recovery_fail"),
    )
    options = dict(
        queue=WorkQueue.GENERAL, worker_id=uuid4(), handlers={"dispatch_probe": handler}
    )
    assert recover_hint(task.run_id, **options)
    assert TaskRun.objects.get(pk=task.run_id).state == "failed"
    final = OperationalLog.objects.get(level="ERROR")
    assert final.event == "task_lease_lost"
    assert final.context == {
        "what": "lease",
        "task_id": str(task.run_id),
        "task_type": "dispatch_probe",
        "attempt": 1,
        "outcome": "failed",
    }
    assert OperationalLog.objects.filter(level="WARNING").count() == 1


def test_a_failure_to_record_a_lost_lease_never_blocks_recovery(monkeypatch):
    """Recording is best effort; the recovery transition still commits."""
    from django.db import DatabaseError

    from parishkit.stewardship.audit import timeouts

    def refused(*args, **kwargs):
        """A login that cannot write the operational log."""
        raise DatabaseError("synthetic refusal")

    monkeypatch.setattr(timeouts, "insert_timeout", refused)
    task = expired_task()
    handler = Handler(
        WorkQueue.GENERAL,
        lambda *args: True,
        lambda context: None,
        recover=lambda status: RecoveryPlan("recovery_fail"),
    )
    options = dict(
        queue=WorkQueue.GENERAL, worker_id=uuid4(), handlers={"dispatch_probe": handler}
    )
    assert recover_hint(task.run_id, **options)
    assert TaskRun.objects.get(pk=task.run_id).state == "failed"


def test_inflight_check_skips_while_the_work_order_lock_is_busy():
    """A busy writer cannot stall a helper's lease check past its deadline (#318).

    While another transaction holds the deployment-wide work-order lock the
    check gives up after INFLIGHT_LOCK_SECONDS and skips the tick; once the
    lock is free it runs in full again and still enforces domain admission.
    """
    import threading
    import time

    from django.db import connections

    from parishkit.stewardship.audit.models import OperationalLog
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.dispatch import INFLIGHT_LOCK_SECONDS

    task, effects, seen = queued(), [True], []
    held, release = threading.Event(), threading.Event()

    def hold():
        """Another session's writer, holding the work-order lock for a while."""
        try:
            with work_transaction():
                held.set()
                release.wait(10)
        finally:
            connections.close_all()

    def execute(context):
        thread = threading.Thread(target=hold)
        thread.start()
        try:
            held.wait(5)
            effects[0] = False  # Would refuse, if the check got to run.
            started = time.monotonic()
            context.check_inflight()
            seen.append(time.monotonic() - started)
        finally:
            release.set()
            thread.join(10)
        with pytest.raises(PermissionError):
            context.check_inflight()
        effects[0] = True
        context.check_inflight()
        context.transition("complete")

    handler = Handler(
        WorkQueue.GENERAL,
        lambda action, status: action != "effect" or effects[0],
        execute,
        scope=work_transaction,
    )
    assert execute_hint(
        task.run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={"dispatch_probe": handler},
    )
    assert INFLIGHT_LOCK_SECONDS <= seen[0] < INFLIGHT_LOCK_SECONDS + 2
    assert TaskRun.objects.get(pk=task.run_id).state == "succeeded"
    # The one skipped tick left a durable entry: what stopped it, the limit
    # and how long the tick ran (#293's timeout log).
    (entry,) = OperationalLog.objects.filter(event="task_timed_out")
    assert entry.level == "WARNING" and entry.schema == "timeout"
    assert entry.context["what"] in {"lock_timeout", "statement_timeout"}
    assert entry.context["task_id"] == str(task.run_id)
    assert entry.context["task_type"] == "dispatch_probe"
    assert entry.context["limit_seconds"] == INFLIGHT_LOCK_SECONDS
    assert entry.context["elapsed_seconds"] >= INFLIGHT_LOCK_SECONDS
    assert "count" not in entry.context


def test_inflight_check_skips_a_slow_statement():
    """statement_timeout bounds a slow admission read, not only lock waits.

    Two skipped ticks leave two entries: the first at once, then a summary
    with their count when the run ends at the next transition.
    """
    from django.db import connection as db

    from parishkit.stewardship.audit.models import OperationalLog
    from parishkit.stewardship.campaigns.work_locks import work_transaction

    task = queued()
    slow = [False]

    def admit(action, status):
        if action == "effect" and slow[0]:
            with db.cursor() as cursor:
                cursor.execute("SELECT pg_sleep(5)")
        return True

    def execute(context):
        slow[0] = True
        context.check_inflight()
        context.check_inflight()
        slow[0] = False
        context.transition("complete")

    handler = Handler(WorkQueue.GENERAL, admit, execute, scope=work_transaction)
    assert execute_hint(
        task.run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={"dispatch_probe": handler},
    )
    first, summary = OperationalLog.objects.filter(event="task_timed_out").order_by(
        "created_at"
    )
    assert first.context["what"] == summary.context["what"] == "statement_timeout"
    assert "count" not in first.context and summary.context["count"] == 2
    assert summary.context["elapsed_seconds"] >= 2


def test_hint_for_unclaimable_task_returns_before_the_handler_scope():
    """Duplicate and early hints skip the work-order lock entirely (#394).

    The handler's scope is where production handlers take the global work
    lock. A hint for a task that is running, finished or not yet due must
    return without entering it; a due queued task still claims through it.
    """
    from contextlib import contextmanager

    entered = []

    @contextmanager
    def scope():
        """Record each entry, as the work-order lock would be taken."""
        entered.append(1)
        yield

    def finish(execution):
        """Complete the claimed task."""
        execution.transition("complete")

    handler = Handler(WorkQueue.GENERAL, lambda *args: True, finish, scope=scope)
    options = dict(
        queue=WorkQueue.GENERAL, worker_id=uuid4(), handlers={"dispatch_probe": handler}
    )
    running = queued()
    change_run(
        run_id=running.run_id,
        expected_version=running.version,
        action="claim",
        actor_id=uuid4(),
        correlation_id=uuid4(),
        admit=lambda *args: True,
        lease_seconds=60,
    )
    assert claim_hint(running.run_id, **options) is None
    assert not recover_hint(running.run_id, **options)
    assert not entered
    # A retry delay in the future is not yet claimable either.
    task = expired_task()
    retry = Handler(
        WorkQueue.GENERAL,
        lambda *args: True,
        finish,
        recover=lambda status: RecoveryPlan("recovery_retry", 30),
    )
    assert recover_hint(
        task.run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={"dispatch_probe": retry},
    )
    assert TaskRun.objects.get(pk=task.run_id).state == "retry_wait"
    assert claim_hint(task.run_id, **options) is None
    assert not entered
    # A due queued task is claimed under the scope and then finished.
    fresh = queued()
    assert execute_hint(fresh.run_id, **options)
    entered.clear()
    assert claim_hint(fresh.run_id, **options) is None
    assert not recover_hint(fresh.run_id, **options)
    assert not entered
    assert TaskRun.objects.get(pk=fresh.run_id).state == "succeeded"
    # An expired lease passes the precheck and is recovered under the scope.
    expired = expired_task()
    assert not recover_hint(expired.run_id, **options)
    assert entered
    assert TaskRun.objects.get(pk=expired.run_id).state == "abandoned"


def window_admit(store, calls):
    """The real bound authority check in front of an always-admitting domain."""
    from parishkit.stewardship.runtime_background import matching_authority

    def admit(*args):
        """Count each admission, then check the live YAML/SQL authority."""
        calls.append(args[0])
        matching_authority(store)
        return True

    return admit


@pytest.fixture
def configured_store(tmp_path):
    """A real applied configuration for the bound authority check."""
    from parishkit.stewardship.accounts.authority import AuthorityStore
    from parishkit.stewardship.accounts.configuration_installation import (
        prepare_initial_configuration,
    )
    from parishkit.stewardship.accounts.configuration_schema import validate_sections

    from ..configuration_factory import configuration_version

    store = AuthorityStore(tmp_path, validate_sections)
    prepare_initial_configuration(
        store,
        configuration_version(),
        testing_recipient="testing@example.org",
        actor_id=uuid4(),
        correlation_id=uuid4(),
    )
    return store


def test_claim_and_recovery_wait_out_an_activation(configured_store):
    """A hint or recovery pass in the window waits and proceeds (#429)."""
    from .activation_builders import activation_window

    store, calls = configured_store, []
    task = queued()
    handler = Handler(
        WorkQueue.GENERAL,
        window_admit(store, calls),
        lambda context: context.transition("complete"),
    )
    arguments = dict(
        queue=WorkQueue.GENERAL, worker_id=uuid4(), handlers={"dispatch_probe": handler}
    )
    with activation_window(store, closes_after=0.5):
        assert execute_hint(task.run_id, **arguments)
    assert TaskRun.objects.get(pk=task.run_id).state == "succeeded"
    assert calls.count("claim") > 1
    task, calls[:] = expired_task(), []
    handler = Handler(
        WorkQueue.GENERAL,
        window_admit(store, calls),
        lambda context: None,
        recover=lambda status: RecoveryPlan("recovery_retry", 30),
    )
    arguments["handlers"] = {"dispatch_probe": handler}
    with activation_window(store, closes_after=0.5):
        assert recover_hint(task.run_id, **arguments)
    assert TaskRun.objects.get(pk=task.run_id).state == "retry_wait"
    assert calls.count("lease_expired") > 1


def test_inflight_check_in_the_window_is_unverified(configured_store):
    """A real bound in-flight check counts the window's tick as unverified."""
    from .activation_builders import activation_window

    store, seen = configured_store, []
    task = queued()

    def execute(context):
        """Check once inside the window and once after it closes."""
        with activation_window(store):
            seen.append(context.check_inflight())
        seen.append(context.check_inflight())
        context.transition("complete")

    handler = Handler(WorkQueue.GENERAL, window_admit(store, []), execute)
    assert execute_hint(
        task.run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={"dispatch_probe": handler},
    )
    assert seen == [False, True]


def test_installation_running_reads_the_installer_lock():
    """The stuck-activation check sees the installer's session lock."""
    from parishkit.stewardship.accounts.installation_lock import installation_lock
    from parishkit.stewardship.activation_hold import installation_running

    assert not installation_running()
    with installation_lock():
        assert installation_running()
    assert not installation_running()
