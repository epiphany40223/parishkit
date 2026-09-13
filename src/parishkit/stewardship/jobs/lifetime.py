"""Bounded worker heartbeats, cooperative drainage and exact source renewal.

The heartbeat owns one short-lived thread-local database connection. It never
performs provider I/O, declares an outcome or revives an expired claim. Handlers
must check the control before every new bounded external operation; SQL fences
and domain admission still protect each durable effect. A stopped worker may
finish its current safe unit, but must not start another external operation.
"""

from contextlib import contextmanager
from threading import Event, RLock, Thread

from django.db import connection, connections, transaction

from parishkit.stewardship.observability import emit_failure

PULSE_SECONDS = 20
# This is a process-drain budget, not a promise that multiple independently
# timed SQL statements finish within one statement timeout. Exhaustion must
# terminate the consumer rather than let a lingering renewer overlap new work.
RENEWAL_DRAIN_SECONDS = 30


class RenewalDrainFailure(BaseException):
    """Unconfirmed renewal-thread drainage must escape the broker task catcher."""


class ExecutionInterrupted(RuntimeError):
    """Drainage or lost renewal prohibits starting another unit of work."""


class ExecutionControl:
    """Process-local cooperation is additional to, never a substitute for, fencing."""

    def __init__(self):
        """Keep signals and the optional source claim private to one execution."""
        self.lock = RLock()
        self.stop = Event()
        self.failed = Event()
        self.finished = Event()
        self.active = False
        self.started = False
        self.source_claim = None

    def check(self, *, allow_drain=False):
        """Reject lost ownership, or a new unit after a graceful-stop request."""
        if (
            self.failed.is_set()
            or self.finished.is_set()
            or (self.stop.is_set() and not allow_drain)
        ):
            raise ExecutionInterrupted("Worker execution must stop at this boundary.")


@contextmanager
def maintain_source(execution, claim):
    """Renew one exact source claim along with the task during external work.

    Enter after acquisition and leave before releasing the source lease. The
    shared lock drains any concurrent renewal before release; a heartbeat can
    never renew a released/replaced source claim accidentally.
    """
    from parishkit.stewardship.source.leases import SourceClaim

    control = execution.control
    if not isinstance(claim, SourceClaim) or (
        claim.task_id,
        claim.task_fence,
        claim.worker_id,
    ) != (
        execution.claim.run_id,
        execution.claim.fence,
        execution.claim.worker_id,
    ):
        raise ValueError("Source renewal requires this exact execution claim.")
    with control.lock:
        control.check()
        if not control.active or control.source_claim is not None:
            raise ExecutionInterrupted("Source renewal requires one active lifetime.")
        control.source_claim = claim
    try:
        yield
    finally:
        with control.lock:
            control.source_claim = None


def renew_once(execution):
    """Renew task/source atomically with finite SQL waits and no external work."""
    from parishkit.stewardship.source.leases import renew_source

    control = execution.control
    with control.lock:
        if control.finished.is_set():
            return
        control.check(allow_drain=True)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '2s'")
                cursor.execute("SET LOCAL statement_timeout = '5s'")
            with execution.handler.scope():
                execution.heartbeat()
                if control.source_claim is not None:
                    renew_source(control.source_claim)
        # A long finite source read blocks Celery's solo-loop timer. Publish
        # process liveness only after successful independent SQL renewal, never
        # from an unconditional heartbeat thread or inside its transaction.
        if execution.handler.pulse is not None:
            execution.handler.pulse()


def _renewal_loop(execution, done):
    """One disposable SQL connection per pulse; no global settings are mutated."""
    previous = connection.settings_dict
    connection.settings_dict = {
        **previous,
        "OPTIONS": {**previous.get("OPTIONS", {}), "connect_timeout": 3},
    }
    try:
        while not done.wait(PULSE_SECONDS):
            try:
                renew_once(execution)
            finally:
                connections.close_all()
            if execution.control.finished.is_set():
                return
    except Exception as error:
        execution.control.failed.set()
        emit_failure(error)
    finally:
        connections.close_all()
        connection.settings_dict = previous


@contextmanager
def maintain_execution(execution, *, stop=None):
    """Keep leases live during a bounded handler, then stop renewal on every exit.

    A crash/exception still leaves its durable claim for reconciliation. SIGTERM
    is not proof of cancellation: the runtime's stop event only requests that
    handlers finish their current safe unit and stop starting new work.
    """
    if connection.in_atomic_block:
        raise ExecutionInterrupted("Worker lifetime cannot hold a transaction.")
    if stop is not None and not isinstance(stop, Event):
        raise ValueError("Worker drainage requires a process-owned stop event.")
    control = execution.control
    with control.lock:
        if control.started:
            raise ExecutionInterrupted("An execution lifetime cannot be reused.")
        if stop is not None:
            control.stop = stop
        control.check()
        control.active = True
        control.started = True
    done = Event()
    thread = Thread(
        target=_renewal_loop,
        args=(execution, done),
        name="stewardship-lease-renewal",
        daemon=True,
    )
    try:
        thread.start()
        yield
    finally:
        done.set()
        if thread.ident is not None:
            thread.join(timeout=RENEWAL_DRAIN_SECONDS)
        control.active = False
        if thread.is_alive():
            control.failed.set()
            error = RenewalDrainFailure("Worker renewal did not drain in time.")
            emit_failure(error)
            raise error
