"""Source work has its own queue and consumer process in the worker (#336).

A ParishSoft refresh takes minutes; on the one-at-a-time general consumer it
held up exports and operational collection. These tests pin the routing, the
per-process queue split, the unchanged Valkey ACL (so ``retarget-image`` can
deploy it) and the sibling process's supervision.
"""

import signal
import subprocess
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import runtime_process
from parishkit.stewardship.deployment import ServiceRole, ValkeyConfiguration
from parishkit.stewardship.jobs.broker import BrokerRuntime, build_broker
from parishkit.stewardship.jobs.processes import serve_consumer, worker_options
from parishkit.stewardship.jobs.queues import (
    ROLE_QUEUES,
    SOURCE_QUEUES,
    WorkQueue,
    exchange,
)
from parishkit.stewardship.runtime_valkey import broker_acl

from .test_runtime_topology import configuration_at

GENERAL_PROCESS = ROLE_QUEUES[ServiceRole.WORKER] - SOURCE_QUEUES


def _broker(service, queues=None):
    """A lazy client with a synthetic password; no network operations."""
    return build_broker(
        endpoint=ValkeyConfiguration("valkey", 6379, 0, None),
        password="synthetic-private-password",
        service=service,
        handlers={},
        queues=queues,
    )


def test_source_work_routes_to_the_source_queue_and_short_work_does_not():
    """The scheduler publishes each hint to the queue its handler names."""
    from parishkit.stewardship.jobs.operational_collection import (
        collection_handler,
    )
    from parishkit.stewardship.jobs.operational_fanout import fanout_handler
    from parishkit.stewardship.jobs.operational_slack_tasks import slack_handler
    from parishkit.stewardship.reports.export_services import (
        TASK_TYPE as REPORT_EXPORT,
    )
    from parishkit.stewardship.reports.fact_tasks import TASK_TYPE as REPORT_FACTS
    from parishkit.stewardship.runtime_background import scheduler_handlers
    from parishkit.stewardship.source.requests import TASK_TYPE as REFRESH
    from parishkit.stewardship.source.setup_admission import TASK_TYPE as SETUP_LOAD
    from parishkit.stewardship.source.setup_disposal import TASK_TYPE as CLEANUP
    from parishkit.stewardship.source.setup_final_execution import (
        finalization_handler,
    )

    handlers = scheduler_handlers()
    source = {REFRESH, SETUP_LOAD, CLEANUP}
    assert {name for name, h in handlers.items() if h.queue is WorkQueue.SOURCE} == (
        source
    )
    assert handlers[REPORT_EXPORT].queue is WorkQueue.GENERAL
    assert handlers[REPORT_FACTS].queue is WorkQueue.GENERAL
    assert finalization_handler(Mock(), scheduler=True).queue is WorkQueue.SOURCE
    # Operational collection and alert fanout stay on the general queue.
    assert collection_handler(scheduler=True).queue is WorkQueue.GENERAL
    assert fanout_handler(Mock(), scheduler=True).queue is WorkQueue.GENERAL
    assert (
        slack_handler(Mock(), credential_path=None, scheduler=True).queue
        is WorkQueue.GENERAL
    )


def test_worker_handlers_bind_source_work_to_the_source_queue():
    """The worker's own registry agrees with the scheduler's routing."""
    from parishkit.stewardship.source.execution import refresh_handler
    from parishkit.stewardship.source.setup_cleanup import cleanup_handler
    from parishkit.stewardship.source.setup_execution import setup_source_handler

    refresh = refresh_handler(credential_path=Path("/synthetic"), reconcile=Mock())
    for handler in (refresh, setup_source_handler(), cleanup_handler()):
        assert handler.queue is WorkQueue.SOURCE


@pytest.mark.parametrize(
    "queues,expected",
    [
        (SOURCE_QUEUES, ["general-source"]),
        (GENERAL_PROCESS, ["general", "restore-general"]),
        (None, ["general", "general-source", "restore-general"]),
    ],
)
def test_each_worker_process_consumes_only_its_share(queues, expected):
    """The two processes split the worker's queues without overlap."""
    assert worker_options(_broker(ServiceRole.WORKER, queues))["queues"] == expected


@pytest.mark.parametrize(
    "service,queues",
    [
        (ServiceRole.WORKER, frozenset({WorkQueue.MAIL})),
        (ServiceRole.WORKER, frozenset()),
        (ServiceRole.MAIL_DISPATCH, SOURCE_QUEUES),
        (ServiceRole.WORKER, {WorkQueue.SOURCE}),
    ],
)
def test_queue_selection_can_only_narrow_a_role(service, queues):
    """No startup option can add a queue the role's broker identity lacks."""
    with pytest.raises(ConfigError):
        _broker(service, queues)
    with pytest.raises(ConfigError):
        worker_options(BrokerRuntime(Mock(), service, queues=queues))


def test_source_queue_shares_the_general_exchange():
    """Publishers route by routing key within the ACL-granted general binding."""
    runtime = _broker(ServiceRole.SCHEDULER)
    queues = {queue.name: queue for queue in runtime.app.conf.task_queues}
    source = queues[WorkQueue.SOURCE.value]
    assert source.exchange.name == "general" and source.routing_key == "general-source"
    assert queues["general"].routing_key == "general"
    assert WorkQueue.SOURCE.value.startswith(WorkQueue.GENERAL.value)
    assert exchange(WorkQueue.MAIL) is WorkQueue.MAIL


@pytest.mark.parametrize("role", [ServiceRole.WORKER, ServiceRole.SCHEDULER])
def test_valkey_acl_is_unchanged_so_retarget_can_deploy_the_queue(role):
    """Retarget keeps the once-generated ACL, so the queue must need no new key."""
    output = broker_acl(role, b"synthetic-private-password").decode()
    assert "general-source" not in output
    assert "~stewardship:broker:v1:general*" in output
    assert "~stewardship:broker:v1:_kombu.binding.general " in output


def _serve(monkeypatch, runtime, controller_hook, **options):
    """Run serve_consumer with a fake controller that exposes the timer."""
    from celery.worker import worker

    scheduled = {}

    def controller(*, app, ready_callback, **ignored):
        """Record repeating timer callbacks instead of starting Celery."""
        consumer = SimpleNamespace(
            timer=SimpleNamespace(
                call_repeatedly=lambda seconds, function: scheduled.setdefault(
                    seconds, function
                )
            )
        )
        ready_callback(consumer)
        controller_hook(scheduled)
        return SimpleNamespace(start=Mock(), exitcode=0)

    monkeypatch.setattr(worker, "WorkController", controller)
    return serve_consumer(runtime, **options)


def test_idle_callback_never_runs_beside_an_executing_message(monkeypatch):
    """Skipping it while busy keeps each process to one task + one renewal."""
    from parishkit.stewardship.jobs import processes

    stop, idle = Event(), Mock()
    built = _broker(ServiceRole.WORKER, GENERAL_PROCESS)
    runtime = BrokerRuntime(built.app, built.service, stop, built.queues, built.busy)
    monkeypatch.setattr(processes.connections, "close_all", Mock())

    def drive(scheduled):
        """Run the idle pass once while a message holds the lock, once after."""
        maintain = scheduled[processes.IDLE_SECONDS]
        with runtime.busy:
            maintain()
        idle.assert_not_called()
        maintain()
        idle.assert_called_once()

    assert (
        _serve(
            monkeypatch,
            runtime,
            drive,
            lease=Mock(),
            stop=stop,
            heartbeat=Mock(),
            idle=idle,
        )
        == 0
    )


def test_a_failed_sibling_stops_the_worker_and_stop_requests_reach_it(monkeypatch):
    """The liveness tick checks the sibling; a signal is forwarded to it."""
    from parishkit.stewardship.jobs import processes

    stop, heartbeat, failures = Event(), Mock(), Mock()
    built = _broker(ServiceRole.WORKER, GENERAL_PROCESS)
    runtime = BrokerRuntime(built.app, built.service, stop, built.queues, built.busy)
    companion = Mock()
    companion.check.side_effect = ConfigError("The source consumer exited.")
    monkeypatch.setattr(processes, "emit_failure", failures)

    def drive(scheduled):
        """The tick runs once at readiness; then a SIGTERM arrives."""
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    _serve(
        monkeypatch,
        runtime,
        drive,
        lease=Mock(),
        stop=stop,
        heartbeat=heartbeat,
        companion=companion,
    )
    companion.check.assert_called()
    companion.terminate.assert_called_once()
    heartbeat.assert_not_called()
    failures.assert_called_once()
    assert stop.is_set()


WORKER_ARGV = ["python3", "/usr/local/bin/pk-stewardship", "runtime", "--config", "w"]


def test_source_consumer_reexecutes_the_workers_own_invocation(monkeypatch):
    """The sibling is this process's command line plus --queue source."""
    process = Mock(returncode=None)
    process.poll.return_value = None
    popen = Mock(return_value=process)
    monkeypatch.setattr(runtime_process.subprocess, "Popen", popen)
    consumer = runtime_process.SourceConsumer(drain_seconds=5, argv=WORKER_ARGV)
    command = popen.call_args.args[0]
    assert command[0] == runtime_process.sys.executable
    assert command[1:] == [*WORKER_ARGV[1:], "--queue", "source"]
    consumer.check()  # Running and still inside its startup grace.
    consumer.close()
    process.send_signal.assert_called_once_with(signal.SIGTERM)
    process.wait.assert_called_once_with(timeout=5)
    process.kill.assert_not_called()


def test_the_sibling_keeps_whatever_the_entry_point_set_up():
    """A wrapped entry point (``python -c SCRIPT ARG runtime ...``) is kept whole.

    The compose harness installs its synthetic providers in such a wrapper
    before the CLI runs. A sibling started through another entry point
    (``-m parishkit.stewardship``) skipped it, so in the real containers the
    setup source load reached no fake provider and never finished (#339 CI).
    """
    wrapped = ["python", "-c", "SCRIPT", "[pages]", "runtime", "--config", "w"]
    assert runtime_process.source_command(wrapped)[1:] == [
        *wrapped[1:],
        "--queue",
        "source",
    ]


@pytest.mark.parametrize(
    "argv",
    [
        ["python", "-c", "SCRIPT"],
        [*WORKER_ARGV, "--queue", "source"],
    ],
)
def test_the_sibling_command_needs_an_unnarrowed_runtime(argv):
    """Only a worker runtime can be re-executed, and never twice narrowed."""
    with pytest.raises(ConfigError):
        runtime_process.source_command(argv)


def _consumer(monkeypatch, ages):
    """A SourceConsumer over a fake process whose heartbeat ages are scripted."""
    from parishkit.stewardship import installer_health
    from parishkit.stewardship.audit import timeouts

    process = Mock(returncode=None)
    process.poll.return_value = None
    monkeypatch.setattr(runtime_process.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(installer_health, "heartbeat_age", Mock(side_effect=ages))
    recorded = Mock()
    monkeypatch.setattr(timeouts, "record_timeout", recorded)
    consumer = runtime_process.SourceConsumer(drain_seconds=5, argv=WORKER_ARGV)
    return consumer, process, recorded


def test_one_slow_window_does_not_stop_the_worker_but_is_logged(monkeypatch):
    """A heartbeat late by less than twice the probe limit is only a warning."""
    from parishkit.stewardship.installer_health import MAX_AGE_SECONDS

    slow = MAX_AGE_SECONDS + 30
    consumer, _, recorded = _consumer(monkeypatch, [10, slow, slow + 20, 5])
    for _ in range(4):
        consumer.check()
    # Each stale observation is logged with the limit and how late it was.
    assert [c.kwargs["level"] for c in recorded.call_args_list] == [
        "WARNING",
        "WARNING",
    ]
    first = recorded.call_args_list[0]
    assert first.args[0].value == "helper_timed_out"
    assert first.kwargs["what"] == "source_helper"
    assert first.kwargs["limit_seconds"] == MAX_AGE_SECONDS
    assert first.kwargs["elapsed_seconds"] == slow
    assert [c.kwargs["count"] for c in recorded.call_args_list] == [1, 2]
    # A fresh heartbeat resets the run of stale observations.
    assert consumer.stale == 0


def test_sustained_silence_stops_the_worker_and_is_logged(monkeypatch):
    """Silence past twice the probe limit stops the worker, logged as an error."""
    from parishkit.stewardship.installer_health import MAX_AGE_SECONDS

    limit = runtime_process.SourceConsumer.STALE_LIMIT
    assert limit == 2 * MAX_AGE_SECONDS
    consumer, _, recorded = _consumer(
        monkeypatch, [MAX_AGE_SECONDS + 1, limit - 1, limit + 5]
    )
    consumer.check()
    consumer.check()
    with pytest.raises(ConfigError):
        consumer.check()
    last = recorded.call_args_list[-1]
    assert last.kwargs["level"] == "ERROR"
    assert last.kwargs["limit_seconds"] == limit
    assert last.kwargs["elapsed_seconds"] == limit + 5
    assert last.kwargs["count"] == 3


def test_an_exited_sibling_stops_the_worker_at_once(monkeypatch):
    """Exit needs no staleness window; it is a process failure, not a timeout."""
    consumer, process, recorded = _consumer(monkeypatch, [])
    process.poll.return_value = 1
    with pytest.raises(ConfigError):
        consumer.check()
    recorded.assert_not_called()


def test_startup_counts_from_spawn_until_the_first_heartbeat(monkeypatch):
    """No heartbeat yet is judged by the sibling's age, not as instant failure."""
    consumer, _, recorded = _consumer(monkeypatch, [None, None])
    consumer.check()
    recorded.assert_not_called()
    consumer.started -= runtime_process.SourceConsumer.STALE_LIMIT + 1
    with pytest.raises(ConfigError):
        consumer.check()


def test_a_drain_past_the_grace_is_killed_and_logged(monkeypatch):
    """The kill at the end of the grace period is a logged timeout."""
    consumer, process, recorded = _consumer(monkeypatch, [])
    process.wait.side_effect = [subprocess.TimeoutExpired("x", 5), 0]
    consumer.close()
    process.kill.assert_called_once()
    assert recorded.call_args.kwargs["level"] == "ERROR"
    assert recorded.call_args.kwargs["limit_seconds"] == 5


@pytest.mark.parametrize("overlap,split", [(2, True), (1, False)])
def test_a_budget_too_small_for_two_processes_keeps_one(tmp_path, overlap, split):
    """Two processes need the worker login's six connections."""
    configuration = configuration_at(tmp_path)
    configuration = replace(
        configuration,
        runtime_budget=replace(configuration.runtime_budget, rollout_overlap=overlap),
    )
    assert runtime_process.split_source(configuration) is split


def test_only_the_worker_runs_a_source_consumer(tmp_path):
    """--queue source on another role is refused before any assembly."""
    configuration = replace(
        configuration_at(tmp_path), service_role=ServiceRole.SCHEDULER
    )
    with pytest.raises(ConfigError):
        runtime_process.serve_background(configuration, Mock(), source=True)
