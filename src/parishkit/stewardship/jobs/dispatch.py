"""Internal durable hint execution, independent of broker delivery guarantees.

Only startup-owned handlers belong in this dispatcher. A broker message carries
one UUID, never a callable, provider arguments, privilege flags or task type.
Queue selection is an isolation check, not authorization: the handler rechecks
its actual domain gates and completion/recovery evidence under TaskRun locks.
"""

import time
from collections.abc import Callable
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, field
from threading import Event, Lock
from uuid import UUID

from django.db import OperationalError, connection, transaction
from django.db.models import DateTimeField, Func, Q

from parishkit.stewardship.accounts.authority import AuthorityChanging
from parishkit.stewardship.activation_hold import wait_out_activation
from parishkit.stewardship.storage import StorageInvariantError

from .lifetime import ExecutionControl, maintain_execution, maintain_source
from .models import TaskRun
from .ownership import TaskClaim, database_now, lock_task_claim
from .queues import WorkQueue
from .storage import _locked, _status, change_run

# How long an in-flight ownership check may wait for this worker's control
# lock, or for any one database statement (a lock wait included), before it
# skips that tick instead (see Execution.check_inflight).
INFLIGHT_LOCK_SECONDS = 1
# SQLSTATEs of a statement stopped by lock_timeout and by statement_timeout.
_LIMIT_STOPS = {"55P03": "lock_timeout", "57014": "statement_timeout"}
# PostgreSQL wall time, as database_now() reads it, for the lock-free hint
# prechecks below. Not now(): that is the transaction's start time.
_CLOCK = Func(function="clock_timestamp", output_field=DateTimeField())
# Rows a hint may claim, and rows a recovery hint may act on.
_CLAIMABLE = Q(state__in=("queued", "retry_wait"), not_before__lte=_CLOCK)
_RECOVERABLE = Q(state="abandoned") | Q(state="running", lease_expires_at__lte=_CLOCK)


def _hint_actionable(run_id, condition):
    """Whether a hinted row looks actionable now, read without any lock (#394).

    Claiming or recovering from a hint takes the handler's scope, which for
    most tasks is the deployment-wide work-order lock. During a large send
    most hints a consumer takes are duplicates for tasks already claimed or
    finished, and taking that lock only to find so held up everyone else.
    This read lets those hints return early. It decides only that nothing
    is to be done, which the authoritative check under the locks would also
    decide for the same row; a row that becomes actionable after this read
    is due work, and the scheduler's next scan hints it again.
    """
    return TaskRun.objects.filter(condition, pk=run_id).exists()


def record_inflight_skip(facts):
    """Persist skipped in-flight lease checks: what stopped, limit, time (#318).

    ``facts`` has the task id, ``what`` stopped the check (``control_lock``,
    ``lock_timeout`` or ``statement_timeout``), ``limit_seconds``,
    ``elapsed_seconds`` and ``skipped``, the number of ticks it covers; a
    summary of more than one tick carries that number as ``count``. The
    write is a WARNING in the timeout log (#293), waited on for at most two
    seconds so it never holds up the helper it reports on.
    """
    from parishkit.stewardship.audit.timeouts import record_timeout_within
    from parishkit.stewardship.observability import Event

    record_timeout_within(
        2,
        Event.TASK_TIMED_OUT,
        level="WARNING",
        what=facts["what"],
        task_id=facts["task_id"],
        limit_seconds=facts["limit_seconds"],
        elapsed_seconds=facts["elapsed_seconds"],
        count=facts["skipped"] if facts["skipped"] > 1 else None,
    )


@dataclass
class InflightSkips:
    """This execution's run of skipped in-flight checks, reported sparingly.

    The first skip of a run is reported at once; the rest are summed and
    reported (count and total time) when the run ends: at the next full
    check or the execution's next transition. Checks tick four times a
    second, so reporting each one would flood the log under contention.
    """

    lock: Lock = field(default_factory=Lock)
    facts: dict | None = None

    def skipped(self, execution, what, limit, elapsed):
        """Count one skipped tick, reporting it when it starts a run."""
        with self.lock:
            first = self.facts is None
            if first:
                self.facts = execution.task_facts() | {
                    "what": what,
                    "limit_seconds": limit,
                    "elapsed_seconds": 0.0,
                    "skipped": 0,
                }
            self.facts["skipped"] += 1
            self.facts["elapsed_seconds"] += elapsed
            report = dict(self.facts) if first else None
        if report is not None:
            record_inflight_skip(report)

    def ended(self):
        """Report the rest of a finished run of skipped ticks, if any."""
        with self.lock:
            facts, self.facts = self.facts, None
        if facts is not None and facts["skipped"] > 1:
            record_inflight_skip(facts)


@dataclass(frozen=True)
class RecoveryPlan:
    """An owning verifier's safe disposition, never inferred from an exception."""

    action: str
    retry_seconds: int | None = None

    def __post_init__(self):
        """Only canonical abandoned-work transitions and bounded retries are valid."""
        if type(self.action) is not str or self.action not in {
            "recovery_retry",
            "recovery_complete",
            "recovery_fail",
            "recovery_cancel",
        }:
            raise ValueError("Unknown task recovery disposition.")
        if self.action == "recovery_retry":
            if (
                type(self.retry_seconds) is not int
                or not 1 <= self.retry_seconds <= 86400
            ):
                raise ValueError("Recovery retry requires a bounded delay.")
        elif self.retry_seconds is not None:
            raise ValueError("Only retry dispositions have a delay.")


@dataclass(frozen=True)
class Handler:
    """Compiled-in owning implementation; never populated from request payloads.

    ``after_transition`` records owning database effects after worker-driven
    Execution transitions and verified recovery dispositions, inside the same
    transaction. Claim and lease-expiry bookkeeping do not invoke this hook.
    It must not perform external I/O:
    callback failure rolls back both those effects and the transition. Admission
    predicates remain read-only and may safely be evaluated more than once.
    """

    queue: WorkQueue
    admit: Callable
    execute: Callable
    recover: Callable | None = None
    scope: Callable = nullcontext
    pulse: Callable | None = None
    after_transition: Callable | None = None

    def __post_init__(self):
        """Reject incomplete handlers before any durable task can be claimed."""
        if (
            not isinstance(self.queue, WorkQueue)
            or not all(
                callable(value) for value in (self.admit, self.execute, self.scope)
            )
            or any(
                value is not None and not callable(value)
                for value in (self.recover, self.pulse, self.after_transition)
            )
        ):
            raise ValueError("A complete internal task handler is required.")


@dataclass(frozen=True)
class Execution:
    """An exact worker claim, with short fenced transactions around checkpoints."""

    claim: TaskClaim
    handler: Handler
    correlation_id: UUID
    control: ExecutionControl = field(
        default_factory=ExecutionControl, repr=False, compare=False
    )
    skips: InflightSkips = field(
        default_factory=InflightSkips, repr=False, compare=False
    )

    def task_facts(self):
        """Identify this execution's task in a timeout report."""
        return {"task_id": self.claim.run_id}

    def check(self):
        """Call before each new external unit; SQL effects also recheck their fences."""
        self.control.check()

    def maintain_source(self, claim):
        """Attach this execution's live source lease through one external-work scope."""
        return maintain_source(self, claim)

    @contextmanager
    def effect(self):
        """Own admission before task/domain locks for one short durable effect.

        Handlers compose source/fact/domain storage inside this scope. No provider
        call belongs here. Each compiled scope must be reentrant and acquire its
        gate before TaskRun; the empty default is only for unrelated task domains.
        Admission waits out a configuration activation in progress (#429); the
        effect itself runs once, after admission.
        """
        # The lock is taken first: each context is entered before the next
        # one's expression is evaluated.
        with (
            self.control.lock,
            wait_out_activation(self._admitted_effect, task_id=self.claim.run_id),
        ):
            yield

    def _admitted_effect(self):
        """Open the scope and transaction and admit one effect, or undo both.

        Returns the open scope and transaction as an ExitStack for the caller's
        ``with``. A refusal rolls the transaction back before it propagates, so
        the attempt can be repeated with nothing held.
        """
        with ExitStack() as stack:
            self.control.check()
            stack.enter_context(self.handler.scope())
            stack.enter_context(transaction.atomic())
            row = lock_task_claim(self.claim)
            if self.handler.admit("effect", _status(row)) is not True:
                raise PermissionError("This task effect is not admitted.")
            return stack.pop_all()

    def transition(self, action, **options):
        """Recheck ownership and owning evidence before any execution transition.

        A transition outside any transaction waits out a configuration
        activation in progress (#429) and then applies once.
        """
        self.skips.ended()
        with self.control.lock:
            result = wait_out_activation(
                lambda: self._transition_once(action, options),
                task_id=self.claim.run_id,
            )
            if result.state != "running":
                self.control.finished.set()
            return result

    def _transition_once(self, action, options):
        """Apply one transition in its own transaction, under the owning scope."""
        self.control.check(allow_drain=True)
        with self.handler.scope(), transaction.atomic():
            row = lock_task_claim(self.claim)
            result = change_run(
                run_id=row.pk,
                expected_version=row.version,
                action=action,
                actor_id=self.claim.worker_id,
                correlation_id=self.correlation_id,
                fence=self.claim.fence,
                admit=self.handler.admit,
                **options,
            )
            if self.handler.after_transition is not None:
                self.handler.after_transition(action, result)
        return result

    def check_inflight(self):
        """Observe an already-started external unit while allowing graceful drain.

        This grants no scope in which to start a new effect. Renewal failure,
        lost SQL ownership or revoked domain admission still stops the helper.

        The check never blocks for long (#318). The deployment-wide work-order
        lock can be busy for seconds on launch day, and a check blocked past
        the helper's deadline used to discard a finished (possibly accepted)
        mail result. The wait for this worker's own control lock (held by a
        heartbeat) is bounded by INFLIGHT_LOCK_SECONDS, and so is each
        database statement in the check, lock waits included (lock_timeout
        and statement_timeout, local to its transaction). The check runs a
        handful of statements (the work-order lock, the task row locks, the
        handler's admission reads), so a tick takes a few seconds at worst.
        When a limit stops it, the tick is skipped and reported (see
        InflightSkips): the helper is still bounded by its deadline, and
        every later transition rechecks ownership under its locks.

        Returns True when ownership was verified in SQL, and False when a
        limit skipped the tick, so a caller that spaces out its checks does
        not count a skipped one as verified. A configuration activation in
        progress (#429) also returns False: the tick proves nothing, and the
        next one (or the next transition) checks again once it commits.
        """
        if connection.in_atomic_block:
            # The transaction-local limits below would leak into the caller's
            # transaction, and its locks would be held across the helper.
            raise StorageInvariantError("In-flight checks run outside transactions.")
        started = time.monotonic()
        if not self.control.lock.acquire(timeout=INFLIGHT_LOCK_SECONDS):
            self.skips.skipped(
                self,
                "control_lock",
                INFLIGHT_LOCK_SECONDS,
                time.monotonic() - started,
            )
            return False
        try:
            self.control.check(allow_drain=True)
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT set_config('lock_timeout', %s, true),"
                            " set_config('statement_timeout', %s, true)",
                            [f"{INFLIGHT_LOCK_SECONDS}s"] * 2,
                        )
                    with self.handler.scope(), transaction.atomic():
                        row = lock_task_claim(self.claim)
                        admitted = self.handler.admit("effect", _status(row))
            except AuthorityChanging:
                return False
            except OperationalError as error:
                what = _LIMIT_STOPS.get(getattr(error.__cause__, "sqlstate", None))
                if what is None:
                    raise
                self.skips.skipped(
                    self, what, INFLIGHT_LOCK_SECONDS, time.monotonic() - started
                )
                return False
            self.skips.ended()
            if admitted is not True:
                raise PermissionError("This in-flight task is not admitted.")
            return True
        finally:
            self.control.lock.release()

    def heartbeat(self, *, seconds=60):
        """Only a still-current owner can extend its lease between bounded steps."""
        return self.transition("heartbeat", lease_seconds=seconds)

    def progress(self, current, total, *, phase=None):
        """Persist counts and optional typed phase, never arbitrary worker messages."""
        return self.transition("progress", progress=(current, total), phase=phase)


def claim_hint(run_id, *, queue, worker_id, handlers):
    """Resolve immutable type from PostgreSQL and ignore duplicate/stale/early hints.

    ``handlers`` is the internal startup registry. An unsupported task is denied,
    not dynamically imported. Domain admission owns campaign/restore/purge rules;
    each handler's execute method owns rechecks at every later effect boundary.
    """
    if not isinstance(run_id, UUID) or not isinstance(worker_id, UUID):
        raise ValueError("Task hints and worker identities must be canonical UUIDs.")
    if not isinstance(queue, WorkQueue):
        raise ValueError("The admitted service queue is required.")
    original = TaskRun.objects.filter(pk=run_id).first()
    if original is None:
        return None
    handler = handlers.get(original.task_type)
    if not isinstance(handler, Handler) or handler.queue is not queue:
        raise PermissionError("This task is unavailable to the admitted consumer.")
    if not _hint_actionable(run_id, _CLAIMABLE):
        return None
    with handler.scope(), _locked(original.correlation_id, root_id=original.root_id):
        row = TaskRun.objects.select_for_update().get(pk=run_id)
        # The authoritative check, repeated under the locks.
        if row.state not in {"queued", "retry_wait"} or row.not_before > database_now():
            return None
        status = change_run(
            run_id=row.pk,
            action="claim",
            expected_version=row.version,
            actor_id=worker_id,
            correlation_id=row.correlation_id,
            admit=handler.admit,
            lease_seconds=60,
        )
        return Execution(
            TaskClaim(status.run_id, status.fence, worker_id),
            handler,
            row.correlation_id,
        )


def execute_hint(run_id, *, queue, worker_id, handlers, stop=None):
    """Run outside a transaction; returning alone never proves task completion.

    The owning handler explicitly records a verified completion/retry/failure or
    cancellation through Execution. A crash or unexpected return leaves the live
    claim and its checkpoints intact for ordinary expiry/reconciliation. Never
    translate an exception into a false success or a blind external-action retry.
    """
    if stop is not None and not isinstance(stop, Event):
        raise ValueError("Worker drainage requires a process-owned stop event.")
    if transaction.get_connection().in_atomic_block:
        raise StorageInvariantError(
            "Worker execution must not hold a database transaction."
        )
    if stop is not None and stop.is_set():
        return False
    # The claim is one transaction; it waits out an activation in progress.
    execution = wait_out_activation(
        lambda: claim_hint(run_id, queue=queue, worker_id=worker_id, handlers=handlers),
        task_id=run_id,
    )
    if execution is None:
        return False
    from parishkit.stewardship.observability import task_scope

    # Bind the task so a helper stopped deep inside the handler can say which
    # task it was serving (#293).
    with task_scope(execution.claim.run_id), maintain_execution(execution, stop=stop):
        execution.handler.execute(execution)
    return True


def recover_hint(run_id, *, queue, worker_id, handlers):
    """Fence expired execution and apply only a domain-verified recovery plan.

    The recovery callback reads durable checkpoints/effect evidence under these
    locks. It performs no provider I/O. None leaves uncertain work abandoned;
    domains needing external evidence schedule their separate reconciliation.
    After recovery the ordinary scheduler supplies a new execution hint when
    due; this function never performs an external action or skips retry delay.
    The whole pass is one transaction, so it waits out a configuration
    activation in progress (#429) and runs again.
    """
    return wait_out_activation(
        lambda: _recover_once(
            run_id, queue=queue, worker_id=worker_id, handlers=handlers
        ),
        task_id=run_id,
    )


def _recover_once(run_id, *, queue, worker_id, handlers):
    """Apply one recovery pass; see recover_hint."""
    if not isinstance(run_id, UUID) or not isinstance(worker_id, UUID):
        raise ValueError("Task hints and worker identities must be canonical UUIDs.")
    if not isinstance(queue, WorkQueue):
        raise ValueError("The admitted service queue is required.")
    original = TaskRun.objects.filter(pk=run_id).first()
    if original is None:
        return False
    handler = handlers.get(original.task_type)
    if not isinstance(handler, Handler) or handler.queue is not queue:
        raise PermissionError("This task is unavailable to the admitted consumer.")
    if not _hint_actionable(run_id, _RECOVERABLE):
        return False
    with handler.scope(), _locked(original.correlation_id, root_id=original.root_id):
        row = TaskRun.objects.select_for_update().get(pk=run_id)
        now = database_now()
        if row.state == "running" and row.lease_expires_at <= now:
            lost = _lease_facts(row, now)
            change_run(
                run_id=row.pk,
                expected_version=row.version,
                action="lease_expired",
                actor_id=None,
                correlation_id=row.correlation_id,
                admit=handler.admit,
            )
            row.refresh_from_db()
            _record_lease_lost(lost, level="WARNING")
        if row.state != "abandoned" or handler.recover is None:
            return False
        plan = handler.recover(_status(row))
        if plan is None:
            return False
        if not isinstance(plan, RecoveryPlan):
            raise ValueError("Recovery requires a verified internal disposition.")
        result = change_run(
            run_id=row.pk,
            expected_version=row.version,
            action=plan.action,
            actor_id=worker_id,
            correlation_id=row.correlation_id,
            admit=handler.admit,
            retry_seconds=plan.retry_seconds,
        )
        if plan.action == "recovery_fail":
            # The final attempt also lost its lease: say how many attempts ran
            # and that the task has now failed (#293).
            from parishkit.stewardship.audit.schemas import Outcome

            _record_lease_lost(
                {"task_id": row.pk, "task_type": row.task_type, "attempt": row.attempt},
                level="ERROR",
                outcome=Outcome.FAILED,
            )
        if handler.after_transition is not None:
            handler.after_transition(plan.action, result)
        return True


def _lease_facts(row, now):
    """What to record about a lost lease: the task and how long it went silent."""
    last = row.heartbeat_at or row.updated_at
    facts = {"task_id": row.pk, "task_type": row.task_type, "attempt": row.attempt}
    if last is not None:
        facts["elapsed_seconds"] = (now - last).total_seconds()
        if row.lease_expires_at is not None:
            facts["limit_seconds"] = (row.lease_expires_at - last).total_seconds()
    return facts


def _record_lease_lost(facts, *, level, outcome=None):
    """Record that a worker stopped reporting before finishing a task (#293).

    Written in the recovery transaction, so the entry exists exactly when the
    recorded transition committed. A savepoint keeps a failure to record from
    aborting the recovery itself; that failure goes to the process log.
    """
    from parishkit.stewardship.audit.timeouts import insert_timeout, timeout_context
    from parishkit.stewardship.observability import Event, emit_failure

    try:
        context = timeout_context(what="lease", outcome=outcome, **facts)
        with transaction.atomic(), transaction.get_connection().cursor() as cursor:
            insert_timeout(cursor, Event.TASK_LEASE_LOST, level, context)
    except Exception as error:
        emit_failure(error, event=Event.TASK_LEASE_LOST)
