"""Internal durable hint execution, independent of broker delivery guarantees.

Only startup-owned handlers belong in this dispatcher. A broker message carries
one UUID, never a callable, provider arguments, privilege flags or task type.
Queue selection is an isolation check, not authorization: the handler rechecks
its actual domain gates and completion/recovery evidence under TaskRun locks.
"""

from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from threading import Event
from uuid import UUID

from django.db import transaction

from parishkit.stewardship.storage import StorageInvariantError

from .lifetime import ExecutionControl, maintain_execution, maintain_source
from .models import TaskRun
from .ownership import TaskClaim, database_now, lock_task_claim
from .queues import WorkQueue
from .storage import _locked, _status, change_run


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
        """
        with self.control.lock:
            self.control.check()
            with self.handler.scope(), transaction.atomic():
                row = lock_task_claim(self.claim)
                if self.handler.admit("effect", _status(row)) is not True:
                    raise PermissionError("This task effect is not admitted.")
                yield

    def transition(self, action, **options):
        """Recheck ownership and owning evidence before any execution transition."""
        with self.control.lock:
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
            if result.state != "running":
                self.control.finished.set()
            return result

    def check_inflight(self):
        """Observe an already-started external unit while allowing graceful drain.

        This grants no scope in which to start a new effect. Renewal failure,
        lost SQL ownership or revoked domain admission still stops the helper.
        """
        with self.control.lock:
            self.control.check(allow_drain=True)
            with self.handler.scope(), transaction.atomic():
                row = lock_task_claim(self.claim)
                if self.handler.admit("effect", _status(row)) is not True:
                    raise PermissionError("This in-flight task is not admitted.")

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
    with handler.scope(), _locked(original.correlation_id, root_id=original.root_id):
        row = TaskRun.objects.select_for_update().get(pk=run_id)
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
    execution = claim_hint(run_id, queue=queue, worker_id=worker_id, handlers=handlers)
    if execution is None:
        return False
    with maintain_execution(execution, stop=stop):
        execution.handler.execute(execution)
    return True


def recover_hint(run_id, *, queue, worker_id, handlers):
    """Fence expired execution and apply only a domain-verified recovery plan.

    The recovery callback reads durable checkpoints/effect evidence under these
    locks. It performs no provider I/O. None leaves uncertain work abandoned;
    domains needing external evidence schedule their separate reconciliation.
    After recovery the ordinary scheduler supplies a new execution hint when
    due; this function never performs an external action or skips retry delay.
    """
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
    with handler.scope(), _locked(original.correlation_id, root_id=original.root_id):
        row = TaskRun.objects.select_for_update().get(pk=run_id)
        if row.state == "running" and row.lease_expires_at <= database_now():
            change_run(
                run_id=row.pk,
                expected_version=row.version,
                action="lease_expired",
                actor_id=None,
                correlation_id=row.correlation_id,
                admit=handler.admit,
            )
            row.refresh_from_db()
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
        if handler.after_transition is not None:
            handler.after_transition(plan.action, result)
        return True
