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
from .queues import ROLE_QUEUES
from .scheduler import scan_once, scheduler_session


def worker_options(runtime):
    """One execution plus one renewal connection; no fanout, remote control or fork."""
    if (
        not isinstance(runtime, BrokerRuntime)
        or runtime.service not in ROLE_QUEUES
        or not ROLE_QUEUES[runtime.service]
    ):
        raise ConfigError("An isolated consumer transport is required.")
    return {
        "pool": "solo",
        "concurrency": 1,
        "prefetch_multiplier": 1,
        "queues": [queue.value for queue in sorted(ROLE_QUEUES[runtime.service])],
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


def serve_consumer(runtime, *, lease, stop, heartbeat):
    """Use Celery's controller without its CLI banners or ambient signal handlers.

    Celery's ordinary warm-stop flag is set together with our execution event.
    The solo handler finishes its finite current unit and checks the event before
    starting another. Docker's admitted finite grace period bounds final exit;
    forced termination never records a made-up cancellation or external outcome.
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

    def tick():
        """Retain offline exclusion and local liveness without publishing events."""
        try:
            lease.check()
            heartbeat()
        except Exception as error:
            emit_failure(error)
            stop.set()
            state.should_stop = 1

    def ready(consumer):
        """Publish liveness only after the real isolated queues are connected."""
        tick()
        consumer.timer.call_repeatedly(20, tick)

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
    try:
        with scheduler_session() as guard:
            while not stop.is_set():
                lease.check()
                guard.check()
                try:
                    produce(guard)
                    if stop.is_set():
                        break

                    def publish(hint):
                        """Verify offline exclusion before each bounded publication."""
                        lease.check()
                        publish_hint(runtime, hint)

                    result = scan_once(
                        guard,
                        handlers=handlers,
                        publish=publish,
                        cursor=cursor,
                        stop=stop,
                    )
                    cursor = result.cursor
                except Exception as error:
                    emit_failure(error)
                    delay = min(60, delay * 2)
                else:
                    delay = 2
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
