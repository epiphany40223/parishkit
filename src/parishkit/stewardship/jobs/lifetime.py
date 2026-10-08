"""Bounded worker heartbeats, cooperative drainage and exact source renewal.

The heartbeat owns one short-lived thread-local database connection. It never
performs provider I/O, declares an outcome or revives an expired claim. Handlers
must check the control before every new bounded external operation; SQL fences
and domain admission still protect each durable effect. A stopped worker may
finish its current safe unit, but must not start another external operation.
"""

import json
import logging
from contextlib import contextmanager
from threading import Event, RLock, Thread
from time import monotonic

from django.db import connection, connections, transaction

from parishkit.stewardship.observability import emit_failure

# An unconfirmed renewal stops new work, including a transient SQL error,
# with two exceptions: a configuration activation in progress (#429) and a
# renewal stopped by its own lock or statement limit (#386), each retried
# only while the lease the last confirmed renewal (or the claim) committed
# still has time left. That is measured locally from before that commit, so
# it is never inferred from a deadline read back from the database; the
# database fences every effect and refuses a renewal of an expired claim
# either way. The durable bounded retry/reconciliation workflow owns
# recovery once a renewal fails for good. (The phase-2 consolidation review's
# C36 rejected a speculative second renewal on any uncertainty; #386 narrows
# the retry to these known, bounded waits.)
PULSE_SECONDS = 20
# A renewal that met a configuration activation in progress (#429) tries
# again this soon instead of a full pulse later. An activation takes about a
# second, well inside the 60-second lease.
ACTIVATION_RETRY_SECONDS = 2
# A renewal stopped by its own SQL time limit (it waited 2 seconds behind the
# deployment-wide work-order lock, or ran 5) has not lost the lease: the last
# confirmed renewal still holds it (#386, M2). It tries again this soon, as
# long as that lease keeps LEASE_MARGIN_SECONDS in hand when the retry is
# decided. The margin covers this timeout's own entry (up to about 7
# seconds: a 5-second wait for the timeout-log slot, then a write with
# 2-second limits), the 2-second pause and the next renewal's connect (3
# seconds): 12 seconds, leaving 8 for that renewal's statements. The margin
# is a planning figure, not a hard bound: statement_timeout limits each
# statement, not the renewal, and a renewal may first wait for the control
# lock, so the last retry can end after the database lease has expired. The
# database then refuses it (an expired claim is never renewed), and the
# hard bound is local: ExecutionControl.check refuses any new unit of work
# once monotonic time passes the confirmed lease's end (``lease_end``), so
# a handler never starts external work its lease no longer covers. Past
# that point the execution stops, as any unconfirmed renewal does.
TIMEOUT_RETRY_SECONDS = 2
# The lease every renewal confirms: claim_hint's and Execution.heartbeat's
# default. A handler that asks for a longer one only adds time to this.
LEASE_SECONDS = 60
LEASE_MARGIN_SECONDS = 20
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
        # When the claim's transaction began (monotonic), set by claim_hint:
        # its lease runs from then. None (a synthetic execution): the
        # renewal thread's start stands in.
        self.lease_started = None
        # When the renewal in progress acquired the control lock (monotonic),
        # set by renew_once: its SQL, and its lease, start after that.
        self.renewal_started = None
        # When the last confirmed lease ends (monotonic): set by the renewal
        # loop from the claim and each successful renewal. check() refuses
        # new work past it, whatever the renewal thread is doing (#386).
        self.lease_end = None

    def lease_expired(self):
        """Whether monotonic time has passed the confirmed lease's end."""
        return self.lease_end is not None and monotonic() >= self.lease_end

    def check(self, *, allow_drain=False, inflight=False):
        """Reject lost ownership, or a new unit after a graceful-stop request
        or once the confirmed lease has run out locally.

        The lease check is the hard local bound behind the renewal loop's
        retries: a unit already started may still settle (``allow_drain``;
        its SQL fences refuse it if the lease is really gone), but nothing
        new starts. An external operation already running (``inflight``, a
        helper's tick) is stopped once the lease has run out, as after a
        failed renewal: it must not keep working past the lease.
        """
        expired = self.lease_expired()
        if (
            self.failed.is_set()
            or self.finished.is_set()
            or (inflight and expired)
            or ((self.stop.is_set() or expired) and not allow_drain)
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
    """Renew task/source atomically with finite SQL waits and no external work.

    Returns whether it renewed (a finished task is not renewed). The caller
    then publishes liveness with ``pulse``, after this has released the
    control lock.
    """
    from parishkit.stewardship.source.leases import renew_source

    control = execution.control
    with control.lock:
        # After the lock: the handler may hold it through a long effect, and
        # that wait is neither SQL time nor lease time (#386).
        control.renewal_started = monotonic()
        if control.finished.is_set():
            return False
        control.check(allow_drain=True)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '2s'")
                cursor.execute("SET LOCAL statement_timeout = '5s'")
            with execution.handler.scope():
                execution.heartbeat()
                if control.source_claim is not None:
                    renew_source(control.source_claim)
    return True


def pulse(execution):
    """Publish process liveness after a successful renewal.

    A long finite source read blocks Celery's solo-loop timer. Liveness is
    published only after successful independent SQL renewal, never from an
    unconditional heartbeat thread or inside its transaction. It runs
    outside the control lock and after the renewal's timing: the pulse also
    writes the process's service status record (ADM-13), which must neither
    hold up the task's own checks of the lock nor count as renewal wait.

    Time budget: the renewal thread's worst pass is about 21 seconds, inside
    RENEWAL_DRAIN_SECONDS (30): the renewal's own 2-second lock and
    5-second statement limits, then this pulse's status write (1-second
    lock and 2-second statement limits) and, if that times out, its timeout
    entry, which may wait up to five seconds for the process's one shared
    timeout-log slot (audit.timeouts.WRITER_WAIT_SECONDS) before a write
    on a connection with two-second connect and statement limits.
    """
    if execution.handler.pulse is not None:
        execution.handler.pulse()


def _renewal_loop(execution, done):
    """One disposable SQL connection per pulse; no global settings are mutated.

    A renewal that meets a configuration activation in progress (#429) is
    not a lost lease: the lease still has most of its time left, so the loop
    tries again shortly. A mismatch that outlasts the lease ends in ordinary
    lease expiry and its recorded recovery.

    Nor is a renewal stopped by its own lock or statement time limit (#386):
    each is logged (what, limit, elapsed, the task, outcome ``retry``) and
    tried again after TIMEOUT_RETRY_SECONDS while the last confirmed lease
    still has LEASE_MARGIN_SECONDS left. ``confirmed`` is the monotonic time,
    taken before its transaction began, of the last renewal that succeeded,
    and at first of the claim (``control.lease_started``, taken before the
    claim's transaction and its lock waits): the database stamped that lease
    no earlier, so it lasts at least LEASE_SECONDS from there. Any other
    error, a lost claim included, and a timeout with no margin left, stop
    the execution; ``control.failed`` is set before the final timeout entry
    is written, so new work is refused first.
    """
    from parishkit.stewardship.accounts.authority import AuthorityChanging

    from .broker import sql_timeout_kind

    previous = connection.settings_dict
    connection.settings_dict = {
        **previous,
        "OPTIONS": {**previous.get("OPTIONS", {}), "connect_timeout": 3},
    }
    started = None
    # The timeout that ended the loop when its entry is already written.
    logged = None
    control = execution.control
    confirmed = control.lease_started or monotonic()
    control.lease_end = confirmed + LEASE_SECONDS
    pause = PULSE_SECONDS
    try:
        while not done.wait(pause):
            started = monotonic()
            control.renewal_started = None
            pause = PULSE_SECONDS
            try:
                renewed = renew_once(execution)
            except AuthorityChanging:
                # Kept at DEBUG: a lost-lease investigation can see that a
                # renewal met an activation, without a line per settings edit.
                logging.getLogger("parishkit.stewardship.debug").debug(
                    "lease renewal met a configuration activation; retrying"
                )
                pause = ACTIVATION_RETRY_SECONDS
                continue
            except Exception as error:
                if sql_timeout_kind(error) is None:
                    raise
                connections.close_all()
                retry = _margin_left(confirmed)
                if not retry:
                    # New work is refused before the entry is written; the
                    # failure path below does not write it again.
                    control.failed.set()
                    logged = error
                _record_renewal_timeout(
                    execution, error, control.renewal_started or started, retry=retry
                )
                if not retry:
                    raise
                pause = TIMEOUT_RETRY_SECONDS
                continue
            else:
                _renewal_timing(started)
                if renewed:
                    confirmed = control.renewal_started or started
                    control.lease_end = confirmed + LEASE_SECONDS
                    pulse(execution)
            finally:
                connections.close_all()
            if execution.control.finished.is_set():
                return
    except Exception as error:
        # Stop new work first, then record a renewal stopped by its own SQL
        # time limit (#293).
        execution.control.failed.set()
        if error is not logged:
            _record_renewal_timeout(
                execution, error, execution.control.renewal_started or started
            )
        emit_failure(error)
    finally:
        connections.close_all()
        connection.settings_dict = previous


def _renewal_timing(started):
    """Debug-log how long one successful renewal took (BG-12's rehearsal).

    ``wait_ms`` runs from the pulse's start: opening the renewal's
    disposable connection, then queueing behind the work-order lock's
    holders. Observation only: built only when DEBUG is enabled, and any
    failure is swallowed, so it can never affect the lease.
    """
    try:
        debug = logging.getLogger("parishkit.stewardship.debug")
        if debug.isEnabledFor(logging.DEBUG):
            debug.debug(
                "lease renewal timing: %s",
                json.dumps({"wait_ms": round((monotonic() - started) * 1000)}),
            )
    except Exception:  # noqa: S110 - observation must never affect the lease
        pass


# The renewal transaction's own SQL limits (see renew_once), by timeout kind.
RENEWAL_LIMITS = {"lock_timeout": 2, "statement_timeout": 5}


def _margin_left(confirmed):
    """Whether a retry decided now (its timeout entry, the pause, then the
    renewal) keeps LEASE_MARGIN_SECONDS before the last confirmed lease (see
    ``_renewal_loop``) runs out."""
    return (
        monotonic() + TIMEOUT_RETRY_SECONDS
        <= confirmed + LEASE_SECONDS - LEASE_MARGIN_SECONDS
    )


def _record_renewal_timeout(execution, error, started, *, retry=False):
    """Log a renewal that PostgreSQL stopped at its lock or statement limit.

    ``retry`` marks one the loop tolerates and tries again: a WARNING with
    outcome ``retry``. Otherwise it is the ERROR that stopped the execution.
    """
    from parishkit.stewardship.audit.schemas import Outcome
    from parishkit.stewardship.audit.timeouts import record_timeout
    from parishkit.stewardship.observability import Event

    from .broker import sql_timeout_kind

    kind = sql_timeout_kind(error)
    if kind is None:
        return
    record_timeout(
        Event.TASK_TIMED_OUT,
        what=kind,
        level="WARNING" if retry else "ERROR",
        task_id=execution.claim.run_id,
        limit_seconds=RENEWAL_LIMITS.get(kind),
        elapsed_seconds=None if started is None else monotonic() - started,
        outcome=Outcome.RETRY if retry else Outcome.FAILED,
    )


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
        draining = monotonic()
        if thread.ident is not None:
            thread.join(timeout=RENEWAL_DRAIN_SECONDS)
        control.active = False
        if thread.is_alive():
            control.failed.set()
            # Say which task's renewal outlived the drain limit before this
            # consumer is stopped (#293); new work is already refused.
            from parishkit.stewardship.audit.timeouts import record_timeout
            from parishkit.stewardship.observability import Event as LogEvent

            record_timeout(
                LogEvent.TASK_TIMED_OUT,
                what="renewal_drain",
                task_id=execution.claim.run_id,
                limit_seconds=RENEWAL_DRAIN_SECONDS,
                elapsed_seconds=monotonic() - draining,
            )
            error = RenewalDrainFailure("Worker renewal did not drain in time.")
            emit_failure(error)
            raise error
