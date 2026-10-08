"""Process-local stop/renewal coordination rejects unsafe scopes before SQL."""

from dataclasses import replace
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import pytest

from parishkit.stewardship.jobs.dispatch import Execution, Handler, WorkQueue
from parishkit.stewardship.jobs.lifetime import (
    RENEWAL_DRAIN_SECONDS,
    ExecutionInterrupted,
    RenewalDrainFailure,
    maintain_execution,
    renew_once,
)
from parishkit.stewardship.jobs.ownership import TaskClaim
from parishkit.stewardship.source.leases import SourceClaim


def execution():
    """Construct an unclaimed synthetic context; these tests never authorize SQL."""
    return Execution(
        TaskClaim(uuid4(), 1, uuid4()),
        Handler(WorkQueue.GENERAL, lambda *args: False, lambda *args: None),
        uuid4(),
    )


@pytest.mark.parametrize("signal", ["stop", "failed", "finished"])
def test_new_unit_is_denied_after_stop_loss_or_completion(signal):
    """Signals cannot be confused with durable successful completion."""
    context = execution()
    getattr(context.control, signal).set()
    with pytest.raises(ExecutionInterrupted):
        context.check()


@pytest.mark.parametrize("signal", ["failed", "finished"])
def test_inflight_drain_cannot_ignore_lost_ownership_or_completion(signal):
    """Only graceful stop is drainable; a failed/finished owner cannot query SQL."""
    context = execution()
    context.control.stop.set()
    getattr(context.control, signal).set()
    with pytest.raises(ExecutionInterrupted):
        context.check_inflight()


def test_completed_context_does_not_attempt_a_new_heartbeat():
    """A completion racing the timer never tries to renew a terminal TaskRun."""
    context = execution()
    context.control.finished.set()
    renew_once(context)


def test_lifetime_stops_on_exception_and_cannot_be_reentered():
    """One claim has exactly one process-local lifetime, even after an error."""
    context = execution()
    with pytest.raises(RuntimeError, match="synthetic"), maintain_execution(context):
        assert context.control.active
        raise RuntimeError("synthetic")
    assert not context.control.active
    with pytest.raises(ExecutionInterrupted), maintain_execution(context):
        pytest.fail("Lifetime was reused")


def test_nested_lifetime_is_denied():
    """A second renewer cannot change the first lifetime's source attachment."""
    context = execution()
    with (
        maintain_execution(context),
        pytest.raises(ExecutionInterrupted),
        maintain_execution(context),
    ):
        pytest.fail("Nested lifetime was admitted")


@pytest.mark.parametrize("stop", [True, "stop", object()])
def test_lifetime_rejects_non_event_drainage(stop):
    """No loose flag or caller string substitutes for the process-owned event."""
    with pytest.raises(ValueError), maintain_execution(execution(), stop=stop):
        pytest.fail("Invalid stop event was admitted")


def test_already_stopping_lifetime_never_starts_renewal():
    """A signal between claim and lifetime entry leaves ordinary expiry recovery."""
    context, stop = execution(), Event()
    stop.set()
    with pytest.raises(ExecutionInterrupted), maintain_execution(context, stop=stop):
        pytest.fail("Stopped lifetime was admitted")
    assert not context.control.active


def test_source_attachment_requires_exact_owner_and_active_nonnested_lifetime():
    """An arbitrary, stale or other-task source lease cannot join this renewer."""
    context = execution()
    claim = SourceClaim(
        context.claim.run_id, context.claim.fence, context.claim.worker_id, 1, "full"
    )
    with pytest.raises(ValueError), context.maintain_source(object()):
        pytest.fail("Invalid source claim was attached")
    with pytest.raises(ExecutionInterrupted), context.maintain_source(claim):
        pytest.fail("Inactive source claim was attached")
    with maintain_execution(context), context.maintain_source(claim):
        assert context.control.source_claim is claim
        with pytest.raises(ExecutionInterrupted), context.maintain_source(claim):
            pytest.fail("Nested source claim was attached")
        different = SourceClaim(uuid4(), 1, uuid4(), 1, "full")
        with pytest.raises(ValueError), context.maintain_source(different):
            pytest.fail("Another worker's source claim was attached")
    assert context.control.source_claim is None


@pytest.mark.parametrize("body_fails", [False, True])
def test_undrained_renewer_is_fatal_even_when_handler_already_failed(
    monkeypatch, body_fails
):
    """Ordinary broker error handling must not resume beside an old live renewer."""
    from parishkit.stewardship.deployment import ServiceRole, ValkeyConfiguration
    from parishkit.stewardship.jobs import broker, lifetime

    context = execution()
    thread = Mock(ident=123)
    thread.is_alive.return_value = True
    monkeypatch.setattr(lifetime, "Thread", Mock(return_value=thread))

    def consume(*args, **kwargs):
        """Exercise the real lifetime exit through the registered broker task."""
        with maintain_execution(context):
            if body_fails:
                raise ValueError("synthetic-handler-failure")

    monkeypatch.setattr(broker, "consume_hint", consume)
    runtime = broker.build_broker(
        endpoint=ValkeyConfiguration("valkey", 6379, 0, None),
        password="synthetic-password",
        service=ServiceRole.WORKER,
        handlers={},
    )
    with pytest.raises(RenewalDrainFailure) as caught:
        runtime.app.tasks[broker.HINT_TASK].run(str(uuid4()))
    assert context.control.failed.is_set() and not context.control.active
    thread.join.assert_called_once_with(timeout=RENEWAL_DRAIN_SECONDS)
    if body_fails:
        assert isinstance(caught.value.__context__, ValueError)


def test_inflight_check_skips_while_this_workers_lock_is_busy(monkeypatch):
    """A heartbeat holding the control lock cannot stall a helper's lease check.

    The tick is skipped (no SQL) instead of waiting past the helper's
    deadline (#318).
    """
    import threading
    import time

    from parishkit.stewardship.jobs import dispatch

    monkeypatch.setattr(dispatch, "INFLIGHT_LOCK_SECONDS", 0.1)
    reports = []
    monkeypatch.setattr(dispatch, "record_inflight_skip", reports.append)
    context = execution()
    held, done = threading.Event(), threading.Event()

    def hold():
        with context.control.lock:
            held.set()
            done.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        held.wait(5)
        started = time.monotonic()
        assert context.check_inflight() is False
        assert time.monotonic() - started < 1
        # A run of skips is reported once at its start ...
        assert context.check_inflight() is False
        assert len(reports) == 1
        assert reports[0]["what"] == "control_lock" and reports[0]["skipped"] == 1
        assert reports[0]["task_id"] == context.claim.run_id
        assert reports[0]["limit_seconds"] == 0.1
        assert 0.1 <= reports[0]["elapsed_seconds"] < 1
    finally:
        done.set()
        thread.join(5)
    # ... and summed up when it ends (here, at the next transition).
    context.skips.ended()
    assert len(reports) == 2 and reports[1]["skipped"] == 2
    assert reports[1]["elapsed_seconds"] >= 0.2


def test_inflight_check_refuses_to_run_inside_a_transaction(monkeypatch):
    """Its transaction-local limits must never leak into a caller's transaction."""
    from types import SimpleNamespace

    from parishkit.stewardship.jobs import dispatch
    from parishkit.stewardship.storage import StorageInvariantError

    monkeypatch.setattr(dispatch, "connection", SimpleNamespace(in_atomic_block=True))
    with pytest.raises(StorageInvariantError):
        execution().check_inflight()


@pytest.mark.parametrize("renewed", [True, False])
def test_the_renewal_loop_pulses_after_its_timing_and_outside_the_lock(
    monkeypatch, renewed
):
    """ADM-13: the pulse writes the status record, so it holds no control lock
    and is not counted in the renewal's wait_ms; no renewal, no pulse."""
    from threading import Event as ThreadEvent

    from parishkit.stewardship.jobs import lifetime

    context = execution()
    order, waits, held = [], [], []

    def renew(actual):
        """A renewal that succeeded, or found its task finished."""
        order.append("renew")
        return renewed

    def pulse():
        """Note whether the control lock was held; the loop would swallow an
        assertion raised here, so the test checks the flag afterwards."""
        free = context.control.lock.acquire(blocking=False)
        if free:
            context.control.lock.release()
        held.append(not free)
        order.append("pulse")

    monkeypatch.setattr(lifetime, "renew_once", renew)
    monkeypatch.setattr(lifetime, "_renewal_timing", lambda _: order.append("timed"))
    monkeypatch.setattr("django.db.connections.close_all", lambda: None)
    context = replace(context, handler=replace(context.handler, pulse=pulse))
    stopped = ThreadEvent()

    def one_pass(seconds):
        """Let exactly one pass run; any further wait stops the loop, so a
        regression ends the test instead of hanging it."""
        waits.append(seconds)
        return len(waits) > 1

    monkeypatch.setattr(stopped, "wait", one_pass)
    lifetime._renewal_loop(context, stopped)
    assert order == (["renew", "timed", "pulse"] if renewed else ["renew", "timed"])
    assert held == ([False] if renewed else [])
    assert len(waits) == 2


class SyntheticLockTimeout(Exception):
    """What PostgreSQL raises when a renewal waits past its lock_timeout."""

    sqlstate = "55P03"


@pytest.fixture
def renewal(monkeypatch):
    """Drive ``_renewal_loop`` on a fake clock with scripted renewals.

    ``run(script)`` takes one entry per renewal: an exception to raise,
    or True to renew (after the last entry the task finishes). Each renewal
    takes ``cost`` seconds of the fake clock; each wait adds its pause.
    Returns (pauses, timeout entries, whether the execution failed).
    """
    from parishkit.stewardship.audit import timeouts
    from parishkit.stewardship.jobs import lifetime

    clock = {"now": 1000.0}
    entries, pauses = [], []
    monkeypatch.setattr(lifetime, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(lifetime, "_renewal_timing", lambda _: None)
    monkeypatch.setattr(lifetime, "pulse", lambda execution: None)
    monkeypatch.setattr("django.db.connections.close_all", lambda: None)
    monkeypatch.setattr(lifetime, "emit_failure", lambda error: None)
    contexts = []

    def record(event, **facts):
        """Keep each entry with whether new work was already refused."""
        entries.append({**facts, "failed_first": contexts[-1].control.failed.is_set()})

    monkeypatch.setattr(timeouts, "record_timeout", record)

    def run(script, *, cost=5, stop_after=None, lease_started=None, lock_wait=0):
        context = execution()
        context.control.lease_started = lease_started
        contexts.append(context)
        steps = list(script)

        def renew(actual):
            # The control lock is taken after ``lock_wait``; the renewal's
            # SQL time (``cost``) follows, as in renew_once.
            clock["now"] += lock_wait
            actual.control.renewal_started = clock["now"]
            clock["now"] += cost
            step = steps.pop(0)
            if isinstance(step, BaseException):
                raise step
            if not steps:
                actual.control.finished.set()
            return step

        monkeypatch.setattr(lifetime, "renew_once", renew)

        class Done:
            def wait(self, seconds):
                pauses.append(seconds)
                clock["now"] += seconds
                return stop_after is not None and len(pauses) > stop_after

        lifetime._renewal_loop(context, Done())
        run.last = context
        return pauses, entries, context.control.failed.is_set()

    return run


def test_a_renewal_timeout_is_logged_and_retried(renewal):
    """One lock timeout with the lease's margin intact: a WARNING entry with
    outcome retry, a short pause, then the renewal succeeds (#386, M2)."""
    from parishkit.stewardship.audit.schemas import Outcome
    from parishkit.stewardship.jobs import lifetime

    pauses, entries, failed = renewal([SyntheticLockTimeout(), True])
    assert not failed
    assert pauses == [lifetime.PULSE_SECONDS, lifetime.TIMEOUT_RETRY_SECONDS]
    [entry] = entries
    assert (entry["what"], entry["level"], entry["outcome"]) == (
        "lock_timeout",
        "WARNING",
        Outcome.RETRY,
    )
    assert entry["limit_seconds"] == 2 and entry["elapsed_seconds"] == 5


def test_timeouts_are_retried_only_while_the_lease_keeps_its_margin(renewal):
    """Repeated timeouts: retried until a retry would leave less than the
    margin on the last confirmed lease, then one ERROR entry and the
    execution stops; no entry is written twice."""
    from parishkit.stewardship.audit.schemas import Outcome
    from parishkit.stewardship.jobs import lifetime

    pauses, entries, failed = renewal([SyntheticLockTimeout() for _ in range(20)])
    assert failed
    levels = [entry["level"] for entry in entries]
    assert levels[-1] == "ERROR" and set(levels[:-1]) == {"WARNING"}
    assert entries[-1]["outcome"] == Outcome.FAILED
    # The thread starts at 1000 (no claim time given). Renewals start at
    # 1020, 1027 and 1034 and each times out 5 seconds later; a retry is
    # decided while now + 2 <= 1000 + 60 - 20 = 1040: at 1025 (1027 <= 1040)
    # and 1032 (1034) it is retried, at 1039 (1041) it is not.
    assert len(entries) == 3
    assert pauses == [lifetime.PULSE_SECONDS] + [lifetime.TIMEOUT_RETRY_SECONDS] * 2
    # New work is refused before the final entry is written, not after.
    assert [entry["failed_first"] for entry in entries] == [False, False, True]


def test_the_margin_starts_at_the_claim_not_the_thread(renewal):
    """A claim that began 8 seconds before this thread started (its lock
    waits): its lease runs from then, so the margin ends at 992 + 40 = 1032
    and one retry fewer than from the thread's start is made (1025 is
    retried, 1032 is not)."""
    _, entries, failed = renewal(
        [SyntheticLockTimeout() for _ in range(20)], lease_started=1000.0 - 8
    )
    assert failed and [entry["level"] for entry in entries] == ["WARNING", "ERROR"]


def test_elapsed_is_measured_after_the_control_lock(renewal):
    """A renewal that first waited 10 seconds for the handler's control lock
    reports only its own 5 seconds of SQL as elapsed."""
    _, entries, _ = renewal([SyntheticLockTimeout(), True], lock_wait=10)
    assert entries[0]["elapsed_seconds"] == 5


def test_a_success_restarts_the_margin_from_that_renewal(renewal):
    """The margin runs from the last renewal that succeeded, not the claim."""
    script = [True, SyntheticLockTimeout(), SyntheticLockTimeout(), True]
    pauses, entries, failed = renewal(script)
    assert not failed and [entry["level"] for entry in entries] == ["WARNING"] * 2


def test_an_error_that_is_not_a_timeout_stops_at_once(renewal):
    """A lost claim (or any other error) is never retried."""
    pauses, entries, failed = renewal([RuntimeError("lost"), True])
    assert failed and entries == [] and len(pauses) == 1


def test_a_stop_during_a_retry_pause_ends_the_loop(renewal):
    """Drainage is not held up by a retry: the pause's wait returns at once."""
    pauses, entries, failed = renewal([SyntheticLockTimeout(), True], stop_after=1)
    assert not failed and len(entries) == 1 and len(pauses) == 2


def test_new_work_is_refused_once_the_confirmed_lease_has_run_out(monkeypatch):
    """The hard local bound (#797 review): past the confirmed lease's end no
    new unit starts, though a started one may still settle."""
    from parishkit.stewardship.jobs import lifetime

    context = execution()
    clock = {"now": 100.0}
    monkeypatch.setattr(lifetime, "monotonic", lambda: clock["now"])
    context.control.check()  # no lease recorded yet (synthetic execution)
    context.control.lease_end = 160.0
    context.control.check()
    clock["now"] = 160.0
    with pytest.raises(ExecutionInterrupted):
        context.control.check()
    context.control.check(allow_drain=True)


def test_the_loop_records_each_confirmed_lease_end(renewal):
    """The claim's lease, then each successful renewal's, set lease_end: a
    claim at 1000 (to 1060), then a renewal that took the control lock at
    1020 and succeeded (to 1080)."""
    from parishkit.stewardship.jobs import lifetime

    renewal([True], lease_started=1000.0)
    assert renewal.last.control.lease_end == 1020.0 + lifetime.LEASE_SECONDS


def test_an_inflight_tick_stops_once_the_lease_has_run_out(monkeypatch):
    """A running helper's check refuses past the local lease end (#797
    review), though a settlement (allow_drain) still may run."""
    from parishkit.stewardship.jobs import lifetime

    context = execution()
    clock = {"now": 100.0}
    monkeypatch.setattr(lifetime, "monotonic", lambda: clock["now"])
    context.control.lease_end = 160.0
    context.control.check(allow_drain=True, inflight=True)
    clock["now"] = 160.0
    with pytest.raises(ExecutionInterrupted):
        context.control.check(allow_drain=True, inflight=True)
    context.control.check(allow_drain=True)


def test_the_mail_helper_check_uses_the_inflight_bound(monkeypatch):
    """The Family mail helper's in-flight check stops at the local deadline
    even when its SQL verification is not due."""
    from parishkit.stewardship.jobs import family_mail_delivery_tasks as mail
    from parishkit.stewardship.jobs import lifetime

    context = execution()
    clock = {"now": 100.0}
    monkeypatch.setattr(lifetime, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(mail, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(mail, "_check", lambda execution: True)
    context.control.lease_end = 160.0
    tick = mail._inflight_check(context)
    tick()
    clock["now"] = 160.5
    with pytest.raises(ExecutionInterrupted):
        tick()


def test_a_committed_longer_lease_extends_the_retry_margin(monkeypatch):
    """extend_lease's deadline counts: with a 300 s lease committed, a
    renewal timeout 100 s after the last renewal is still retried (#386, M4)."""
    from parishkit.stewardship.jobs import lifetime

    monkeypatch.setattr(lifetime, "monotonic", lambda: 1100.0)
    assert not lifetime._margin_left(1000.0)
    assert lifetime._margin_left(1000.0, lease_until=1000.0 + 300)
    assert not lifetime._margin_left(1000.0, lease_until=1000.0 + 120)


def test_extend_lease_renews_task_and_attached_source_in_one_transaction(
    monkeypatch,
):
    """Both leases get the longer time, under the control lock, in one
    transaction, and the deadline is recorded; a stopped execution refuses
    first."""
    from contextlib import contextmanager

    from parishkit.stewardship.jobs import lifetime
    from parishkit.stewardship.source import leases

    context = execution()
    calls = []

    @contextmanager
    def atomic():
        calls.append("begin")
        yield
        calls.append("commit")

    monkeypatch.setattr(lifetime.transaction, "atomic", atomic)
    monkeypatch.setattr(lifetime, "monotonic", lambda: 500.0)
    claim = object()
    context.control.source_claim = claim
    monkeypatch.setattr(
        type(context),
        "_transition_once",
        lambda self, action, options: calls.append((action, options)),
    )
    monkeypatch.setattr(
        leases,
        "renew_source",
        lambda actual, *, lease_seconds: calls.append(
            ("source", actual, lease_seconds)
        ),
    )
    lifetime.extend_lease(context, 300)
    assert calls == [
        "begin",
        ("heartbeat", {"lease_seconds": 300}),
        ("source", claim, 300),
        "commit",
    ]
    assert context.control.lease_until == 800.0
    context.control.failed.set()
    with pytest.raises(ExecutionInterrupted):
        lifetime.extend_lease(context, 300)


def test_extend_lease_waits_out_a_configuration_activation(monkeypatch):
    """AuthorityChanging is waited out from outside the transaction, as a
    transition does, instead of failing the refresh (#800 review)."""
    from parishkit.stewardship import activation_hold
    from parishkit.stewardship.accounts.authority import AuthorityChanging
    from parishkit.stewardship.jobs import lifetime

    monkeypatch.setattr(activation_hold, "POLL_SECONDS", 0)
    monkeypatch.setattr(activation_hold, "installation_running", lambda: True)
    context = execution()
    attempts = []

    def transition(self, action, options):
        attempts.append(action)
        if len(attempts) == 1:
            raise AuthorityChanging("synthetic")

    monkeypatch.setattr(type(context), "_transition_once", transition)
    monkeypatch.setattr("django.db.transaction.Atomic.__enter__", lambda self: None)
    monkeypatch.setattr(
        "django.db.transaction.Atomic.__exit__", lambda self, *exc: False
    )
    lifetime.extend_lease(context, 300)
    assert attempts == ["heartbeat", "heartbeat"]
    assert context.control.lease_until is not None


def test_a_renewal_never_shortens_a_longer_committed_lease(monkeypatch):
    """With 200 s left of an extension the renewal asks for 200, not 60;
    once 60 or less is left it asks for 60 and forgets the extension."""
    from parishkit.stewardship.jobs import lifetime

    control = execution().control
    control.renewal_started = 1000.0
    assert lifetime._renewed_seconds(control) == lifetime.LEASE_SECONDS
    control.lease_until = 1200.0
    assert lifetime._renewed_seconds(control) == 200
    control.renewal_started = 1150.0
    assert lifetime._renewed_seconds(control) == lifetime.LEASE_SECONDS
    assert control.lease_until is None


def test_the_local_deadline_counts_a_committed_extension(monkeypatch):
    """With a 300 s extension committed, new work goes on past the 60 s
    renewal lease and stops only once the extension too has run out."""
    from parishkit.stewardship.jobs import lifetime

    context = execution()
    clock = {"now": 200.0}
    monkeypatch.setattr(lifetime, "monotonic", lambda: clock["now"])
    context.control.lease_end = 160.0
    context.control.lease_until = 400.0
    context.control.check()
    clock["now"] = 400.0
    with pytest.raises(ExecutionInterrupted):
        context.control.check()
