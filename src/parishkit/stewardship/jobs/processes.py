"""Closed process loops for already-admitted scheduler/consumer identities.

These helpers do not grant mounts, SQL authority or credentials. Operational
startup owns that admission and supplies its retained lifecycle lease. No CLI
options, module names or broker headers can expand the compiled task registry.
"""

import signal
from threading import Event, current_thread, main_thread

from django.db import connections

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.observability import emit_failure

from .broker import BrokerRuntime, publish_hint
from .due_work_health import DueWorkScan
from .queues import ROLE_QUEUES
from .scheduler import SchedulerOwnershipLost, scan_once, scheduler_session


def worker_options(runtime):
    """One execution plus one renewal connection; no fanout, remote control or fork."""
    if (
        not isinstance(runtime, BrokerRuntime)
        or runtime.service not in ROLE_QUEUES
        or not ROLE_QUEUES[runtime.service]
        or type(runtime.consumed) is not frozenset
        or not runtime.consumed
        or not runtime.consumed <= ROLE_QUEUES[runtime.service]
    ):
        raise ConfigError("An isolated consumer transport is required.")
    return {
        "pool": "solo",
        "concurrency": 1,
        "prefetch_multiplier": 1,
        "queues": [queue.value for queue in sorted(runtime.consumed)],
        "without_gossip": True,
        "without_mingle": True,
        "without_heartbeat": True,
        "beat": False,
        "autoscale": None,
        "statedb": None,
        "pidfile": None,
        "use_eventloop": False,
        "loglevel": "WARNING",
    }


IDLE_SECONDS = 30


def serve_consumer(runtime, *, lease, stop, heartbeat, idle=None, companion=None):
    """Use Celery's controller without its CLI banners or ambient signal handlers.

    Celery's ordinary warm-stop flag is set together with our execution event.
    The solo handler finishes its finite current unit and checks the event before
    starting another. Docker's admitted finite grace period bounds final exit;
    forced termination never records a made-up cancellation or external outcome.
    An optional ``idle`` callback, such as credential acknowledgement, runs on
    Celery's timer every ``IDLE_SECONDS``; its failures are logged and retried
    on the next run rather than stopping the consumer. It is skipped while a
    message executes, so it never adds a SQL connection beside a running task.

    An optional ``companion`` is a sibling consumer process sharing this
    container (see runtime_process.SourceConsumer): a stop request is
    forwarded to it at once so both drain together, and each liveness tick
    checks it, stopping this consumer if the sibling has exited or hung.
    """
    from celery import _state as app_state
    from celery.worker import state
    from celery.worker.worker import WorkController

    if (
        current_thread() is not main_thread()
        or not isinstance(stop, Event)
        or not isinstance(runtime, BrokerRuntime)
        or runtime.stop is not stop
    ):
        raise ConfigError("A consumer requires its main process and stop event.")
    options = worker_options(runtime)
    lease.check()
    previous_stop, previous_terminate = state.should_stop, state.should_terminate
    previous_current = getattr(app_state._tls, "current_app", None)
    previous_default = app_state.default_app
    state.should_stop = state.should_terminate = None

    def stopping(signum, frame):
        """Request warm drainage without provider work, SQL or locks in a signal."""
        stop.set()
        state.should_stop = 0
        if companion is not None:
            companion.terminate()

    def tick():
        """Retain offline exclusion and local liveness without publishing events."""
        try:
            lease.check()
            if companion is not None:
                companion.check()
            heartbeat()
        except Exception as error:
            emit_failure(error)
            stop.set()
            state.should_stop = 1

    def maintain():
        """Run the idle callback on its own connection, never failing the consumer.

        The worker login's connection limit is shared by the container's two
        consumer processes, each budgeted one task and one renewal connection
        (#336), so this runs only while no message is executing.
        """
        if not runtime.busy.acquire(blocking=False):
            return
        try:
            idle()
        except Exception as error:
            emit_failure(error)
        finally:
            # Celery's timer runs outside the task thread; release its
            # thread-local database connection between runs.
            connections.close_all()
            runtime.busy.release()

    def ready(consumer):
        """Publish liveness only after the real isolated queues are connected."""
        tick()
        consumer.timer.call_repeatedly(20, tick)
        if idle is not None:
            consumer.timer.call_repeatedly(IDLE_SECONDS, maintain)

    previous = {
        sig: signal.signal(sig, stopping) for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        if stop.is_set():
            return 0
        # Celery's synchronous trace resolves current_app rather than retaining
        # the controller's app argument. Bind it only for this isolated process
        # lifetime; lazy factory calls must never alter another app's registry.
        runtime.app.set_current()
        runtime.app.set_default()
        controller = WorkController(app=runtime.app, ready_callback=ready, **options)
        controller.start()
        return controller.exitcode or 0
    finally:
        stop.set()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        state.should_stop, state.should_terminate = previous_stop, previous_terminate
        app_state._set_current_app(previous_current)
        app_state.set_default_app(previous_default)
        connections.close_all()
        runtime.app.close()


# The early scan before the producers (#394) looks at no more than this many
# due rows. It only has to get the first few newly due rows (a handful of
# Family messages, say) to the consumers ahead of slow producers; each row it
# checks costs one global work-order lock transaction.
EARLY_SCAN_ROWS = 20


def serve_scheduler(runtime, *, handlers, lease, stop, heartbeat, produce):
    """Own one SQL session for the entire loop; failed hints remain durable work.

    The compiled producer performs bounded durable scheduling, never provider
    I/O. A connection loss exits this process rather than silently reconnecting
    without singleton ownership. Known failed hints reappear in later sweeps.
    """
    if (
        not isinstance(runtime, BrokerRuntime)
        or runtime.service is not ServiceRole.SCHEDULER
        or not isinstance(stop, Event)
        or not callable(produce)
    ):
        raise ConfigError("An isolated scheduler and compiled producer are required.")
    cursor, delay = None, 2
    health = DueWorkScan()

    def publish(hint):
        """Verify offline exclusion before each bounded publication."""
        lease.check()
        publish_hint(runtime, hint)

    def scan(guard, **limit):
        """Publish one fair page of due hints, advancing the shared cursor.

        ``limit`` optionally bounds the page (the early scan); either way the
        next scan continues from where this one stopped.
        """
        nonlocal cursor
        cursor = scan_once(
            guard,
            handlers=handlers,
            publish=publish,
            cursor=cursor,
            stop=stop,
            health=health,
            **limit,
        ).cursor

    def failed(guard, error):
        """Report a failed step and discard this sweep's partial health proof."""
        emit_failure(error)
        # Preserve fair suffix progress, but never let an observed failure
        # bridge two otherwise healthy sample windows.
        try:
            health.interrupted(guard)
        except Exception as health_error:
            emit_failure(health_error)
        finally:
            health.reset()

    try:
        with scheduler_session() as guard:
            while not stop.is_set():
                lease.check()
                guard.check()
                # When no sweep is in progress, publish the first few due
                # hints before the producers run, as well as a full page
                # after them (#394). The producers take the global work-order
                # lock one after another, and while a source refresh holds
                # that lock they can take tens of seconds, which delayed the
                # first hint for newly due work (a few Family messages, say)
                # by that long.
                #
                # The early scan is not free: every production handler checks
                # each due row inside work_transaction(), one global-lock
                # transaction per row, whether or not the row is admitted (a
                # paused send is checked and refused every loop). So it looks
                # at no more than EARLY_SCAN_ROWS rows, and only when a sweep
                # starts. It shares the fair cursor: the late scan continues
                # after the rows it covered, so no row is checked twice unless
                # all due work fit in the early page, and then at most
                # EARLY_SCAN_ROWS rows are. Mid-sweep it is skipped, since the
                # late scan is already partway through the due rows. A hint
                # published twice is harmless anyway: claim_hint() claims a
                # task only while it is queued or retry-waiting and due, under
                # its row lock.
                #
                # Failures in either scan or the producers are counted
                # together: the loop backs off (doubling up to 60 s) once per
                # loop in which any step failed, as it did when there was one
                # scan, and returns to 2 s after a clean loop.
                failures = 0
                if cursor is None:
                    try:
                        scan(guard, limit=EARLY_SCAN_ROWS)
                    except SchedulerOwnershipLost:
                        # Fatal, as below; running the producers first would
                        # only repeat the failure.
                        raise
                    except Exception as error:
                        # Reported like any other failure; the producers
                        # still run.
                        failed(guard, error)
                        failures += 1
                    if stop.is_set():
                        break
                try:
                    produce(guard)
                    if stop.is_set():
                        break
                    scan(guard)
                except Exception as error:
                    failed(guard, error)
                    failures += 1
                delay = min(60, delay * 2) if failures else 2
                # Verify outside the retry block: a lost session is fatal even
                # if a producer or failed query happened to reconnect.
                guard.check()
                lease.check()
                heartbeat()
                stop.wait(delay)
        return 0
    finally:
        connections.close_all()
        runtime.app.close()
