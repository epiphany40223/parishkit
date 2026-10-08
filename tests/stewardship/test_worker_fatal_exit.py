"""A fatal drain or ownership failure stops the consumer process (#386, M5).

``RenewalDrainFailure``, ``ProviderCheckDrainFailure`` and
``ProviderCheckOwnershipLost`` mean this process cannot prove its last
task's renewal thread or helpers stopped. The hint task's boundary logs one
structured CRITICAL line naming the task, refuses further hints and sets
Celery's stop flag to 70; the runtime then closes its sibling consumer as
in any stop, and only if the undrained renewal thread is still alive does
the process end at once. Before this, Celery 5.6.3's solo synchronous loop
already stopped (status 1) without taking another message, but only through
its own shutdown, with the reason in a suppressed unstructured line.
"""

import json
import logging
import subprocess
import sys
import textwrap
from threading import Event, Thread
from uuid import uuid4

import pytest

from parishkit.stewardship import observability
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs import broker
from parishkit.stewardship.jobs.queues import HINT_TASK

from .test_broker import broker as build


@pytest.fixture
def celery_state(monkeypatch):
    """Celery's process-wide stop flag and our fatal flag, restored after."""
    from celery.worker import state

    monkeypatch.setattr(state, "should_stop", None)
    monkeypatch.setattr(broker, "_fatal", Event())
    return state


@pytest.mark.parametrize("failure", broker.FATAL_FAILURES)
def test_each_fatal_failure_stops_the_consumer_after_its_message(
    monkeypatch, caplog, celery_state, failure
):
    """One CRITICAL line naming the class and the task; the runtime's stop
    event and Celery's stop flag (70) are set; nothing exits here."""
    caplog.set_level(logging.INFO, logger="parishkit.stewardship")
    exits = []

    def fatal(args, kwargs, **options):
        raise failure("synthetic")

    monkeypatch.setattr(broker, "consume_hint", fatal)
    monkeypatch.setattr(broker, "_exit", exits.append)
    monkeypatch.setattr("django.db.connections.close_all", lambda: None)
    stop = Event()
    runtime = broker.build_broker(
        endpoint=_endpoint(),
        password="synthetic",
        service=ServiceRole.WORKER,
        handlers={},
        stop=stop,
    )
    task = str(uuid4())
    runtime.app.tasks[HINT_TASK](task)
    assert exits == []
    assert stop.is_set() and celery_state.should_stop == broker.FATAL_EXIT_STATUS
    lines = [
        json.loads(observability.SafeJsonFormatter().format(record))
        for record in caplog.records
    ]
    [line] = [line for line in lines if line["level"] == "CRITICAL"]
    assert line["extra"]["error_class"].endswith(failure.__name__)
    assert line["extra"]["task_id"] == task


def _endpoint():
    """The synthetic Valkey endpoint the broker tests use."""
    from parishkit.stewardship.deployment import ValkeyConfiguration

    return ValkeyConfiguration("valkey", 6379, 0, None)


def test_an_ordinary_failure_does_not_stop(monkeypatch, celery_state):
    """Any Exception is still logged and the consumer goes on."""

    def failing(args, kwargs, **options):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(broker, "consume_hint", failing)
    monkeypatch.setattr("django.db.connections.close_all", lambda: None)
    build(ServiceRole.WORKER).app.tasks[HINT_TASK](str(uuid4()))
    assert celery_state.should_stop is None and not broker._fatal.is_set()


def test_the_top_level_exit_is_hard_only_while_a_renewal_thread_lives(
    monkeypatch, celery_state
):
    """No fatal failure: the runner's status. Fatal, renewal thread gone:
    70, returned. Fatal, renewal thread still alive: os._exit(70)."""
    exits = []
    monkeypatch.setattr(broker, "_exit", exits.append)
    monkeypatch.setattr(broker.logging, "shutdown", lambda: exits.append("flushed"))
    assert broker.exit_if_fatal(0) == 0
    broker._fatal.set()
    assert broker.exit_if_fatal(0) == broker.FATAL_EXIT_STATUS and exits == []
    release = Event()
    thread = Thread(target=release.wait, name="stewardship-lease-renewal", daemon=True)
    thread.start()
    try:
        broker.exit_if_fatal(0)
    finally:
        release.set()
        thread.join()
    assert exits == ["flushed", broker.FATAL_EXIT_STATUS]


# A real solo WorkController, through serve_consumer itself, on Kombu's
# in-memory transport: two hints are queued, and handling the first raises a
# fatal failure. The process must end with status 70 having taken only the
# first. With a sibling, the script then closes it as the runtime's cleanup
# does: the sibling must drain on SIGTERM (it records it), not be killed.
WORKER = textwrap.dedent(
    """
    import signal
    import subprocess
    import sys
    from threading import Event
    from types import SimpleNamespace

    import django

    django.setup()

    from parishkit.stewardship.deployment import ServiceRole, ValkeyConfiguration
    from parishkit.stewardship.jobs import broker, processes
    from parishkit.stewardship.jobs.dispatch import WorkQueue
    from parishkit.stewardship.jobs.lifetime import RenewalDrainFailure
    from parishkit.stewardship.jobs.queues import HINT_TASK
    from parishkit.stewardship.runtime_process import SiblingConsumer

    calls, sibling_log = sys.argv[1], sys.argv[2]

    def fatal(args, kwargs, **options):
        with open(calls, "a") as record:
            record.write(args[0] + "\\n")
        raise RenewalDrainFailure("synthetic")

    broker.consume_hint = fatal
    stop = Event()
    runtime = broker.build_broker(
        endpoint=ValkeyConfiguration("valkey", 6379, 0, None),
        password="synthetic",
        service=ServiceRole.WORKER,
        handlers={},
        stop=stop,
    )
    runtime.app.conf.broker_url = "memory://"
    runtime.app.conf.broker_transport = "memory"
    runtime.app.conf.broker_transport_options = {}
    for hint in sys.argv[3:]:
        runtime.app.send_task(HINT_TASK, args=[hint], queue=WorkQueue.GENERAL.value)

    companion = None
    if sibling_log != "-":
        CHILD = (
            "import signal, sys, time\\n"
            "def drained(number, frame):\\n"
            "    open(sys.argv[1], 'w').write('drained')\\n"
            "    sys.exit(0)\\n"
            "signal.signal(signal.SIGTERM, drained)\\n"
            "open(sys.argv[1], 'w').write('started')\\n"
            "time.sleep(60)\\n"
        )

        class Sibling(SiblingConsumer):
            QUEUE = "source"
            WHAT = "source_helper"

            def __init__(self):
                self.drain_seconds = 30
                self.started = 0
                self.stale = 0
                self.stop_requested = None
                self.process = subprocess.Popen(
                    [sys.executable, "-c", CHILD, sibling_log]
                )

            def check(self):
                pass

        companion = Sibling()

    lease = SimpleNamespace(check=lambda: None)
    try:
        status = processes.serve_consumer(
            runtime,
            lease=lease,
            stop=stop,
            heartbeat=lambda: None,
            companion=companion,
        )
    finally:
        # The runtime's own cleanup: close the sibling, then the top level.
        if companion is not None:
            companion.close()
    sys.exit(broker.exit_if_fatal(status))
    """
)


@pytest.mark.parametrize("sibling", [False, True])
def test_a_real_solo_worker_stops_after_a_fatal_failure(tmp_path, sibling):
    """Exit 70 after the first hint; the second is never taken; a sibling
    consumer is drained (SIGTERM), not killed."""
    calls = tmp_path / "calls"
    sibling_log = tmp_path / "sibling"
    script = tmp_path / "worker.py"
    script.write_text(WORKER)
    first, second = str(uuid4()), str(uuid4())
    arguments = [str(calls), str(sibling_log) if sibling else "-", first, second]
    try:
        result = subprocess.run(
            [sys.executable, str(script), *arguments],
            capture_output=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("the worker went on consuming after a fatal failure")
    assert result.returncode == broker.FATAL_EXIT_STATUS, result.stderr[-2000:]
    assert calls.read_text().split() == [first]
    levels = []
    for line in result.stderr.decode().splitlines():
        try:
            levels.append(json.loads(line).get("level"))
        except ValueError:
            continue
    assert "CRITICAL" in levels
    if sibling:
        assert sibling_log.read_text() == "drained"


def test_the_consumer_is_stopped_before_the_line_is_logged(monkeypatch, celery_state):
    """A failing log write cannot leave the consumer running: every stop
    flag is already set when the CRITICAL line is written."""
    seen = []
    stop = Event()

    def emit(error, **facts):
        seen.append((broker._fatal.is_set(), stop.is_set(), celery_state.should_stop))
        raise RuntimeError("the log write failed")

    monkeypatch.setattr(broker, "emit_failure", emit)
    with pytest.raises(RuntimeError):
        broker.stop_consumer(broker.RenewalDrainFailure("x"), (str(uuid4()),), stop)
    assert seen == [(True, True, broker.FATAL_EXIT_STATUS)]
