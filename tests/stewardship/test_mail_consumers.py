"""Mail dispatch runs two mail consumer processes by default.

One consumer sent Family mail one message at a time; the launch send took
the sum of every message's preparation and SMTP time. These tests pin the
second process's supervision (the #336 worker pattern), the connection budget
that admits it, the one-process fallback, and the shared daily count.
"""

import logging
import signal
import subprocess
from dataclasses import replace
from unittest.mock import Mock

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import runtime_process
from parishkit.stewardship.deployment import (
    MAIL_CONSUMERS_VARIABLE,
    ServiceRole,
    ValkeyConfiguration,
    load_deployment,
)
from parishkit.stewardship.jobs.broker import build_broker
from parishkit.stewardship.jobs.processes import worker_options
from parishkit.stewardship.jobs.queues import ROLE_QUEUES
from parishkit.stewardship.runtime_topology import render_runtime

from .test_deployment import config_file
from .test_runtime_topology import IMAGE, configuration_at

MAIL_ARGV = ["python3", "/usr/local/bin/pk-stewardship", "runtime", "--config", "m"]


def _at(tmp_path, *, consumers=2, overlap=2):
    """A mail-dispatch configuration with a chosen consumer count and budget."""
    configuration = configuration_at(tmp_path)
    return replace(
        configuration,
        service_role=ServiceRole.MAIL_DISPATCH,
        mail_consumers=consumers,
        runtime_budget=replace(configuration.runtime_budget, rollout_overlap=overlap),
    )


@pytest.mark.parametrize(
    "consumers,overlap,split",
    [(2, 2, True), (1, 2, False), (2, 1, False)],
)
def test_two_consumers_need_both_the_setting_and_the_login_budget(
    tmp_path, consumers, overlap, split
):
    """The fallback (1) and a budget too small for six connections keep one."""
    assert (
        runtime_process.split_mail(_at(tmp_path, consumers=consumers, overlap=overlap))
        is split
    )


def test_the_mail_login_budgets_three_connections_per_process(tmp_path):
    """Task, lease renewal and a timeout-log connection, for each process."""
    from parishkit.stewardship.database_provisioning import role_limit

    assert role_limit(_at(tmp_path), ServiceRole.MAIL_DISPATCH) == 6
    assert role_limit(_at(tmp_path, overlap=1), ServiceRole.MAIL_DISPATCH) == 3


def test_both_mail_processes_consume_every_mail_queue():
    """Hints go to whichever process takes them first: no queue is narrowed."""
    runtime = build_broker(
        endpoint=ValkeyConfiguration("valkey", 6379, 0, None),
        password="synthetic-private-password",
        service=ServiceRole.MAIL_DISPATCH,
        handlers={},
    )
    assert runtime.consumed == ROLE_QUEUES[ServiceRole.MAIL_DISPATCH]
    assert set(worker_options(runtime)["queues"]) == {
        queue.value for queue in ROLE_QUEUES[ServiceRole.MAIL_DISPATCH]
    }


def test_the_mail_consumer_reexecutes_the_mail_workers_own_invocation(monkeypatch):
    """The sibling is this process's command line plus --queue mail."""
    process = Mock(returncode=None)
    process.poll.return_value = None
    popen = Mock(return_value=process)
    monkeypatch.setattr(runtime_process.subprocess, "Popen", popen)
    consumer = runtime_process.MailConsumer(drain_seconds=60, argv=MAIL_ARGV)
    command = popen.call_args.args[0]
    assert command[0] == runtime_process.sys.executable
    assert command[1:] == [*MAIL_ARGV[1:], "--queue", "mail"]
    consumer.check()  # Running and still inside its startup grace.
    consumer.close()
    process.send_signal.assert_called_once_with(signal.SIGTERM)
    # What is left of the grace, less the kill margin.
    margin = runtime_process.SiblingConsumer.KILL_MARGIN
    assert process.wait.call_args.kwargs["timeout"] == pytest.approx(60 - margin, abs=1)
    process.kill.assert_not_called()


def _consumer(monkeypatch, ages):
    """A MailConsumer over a fake process whose heartbeat ages are scripted."""
    from parishkit.stewardship import installer_health
    from parishkit.stewardship.audit import timeouts

    process = Mock(returncode=None)
    process.poll.return_value = None
    monkeypatch.setattr(runtime_process.subprocess, "Popen", Mock(return_value=process))
    ages = Mock(side_effect=ages)
    monkeypatch.setattr(installer_health, "heartbeat_age", ages)
    recorded = Mock()
    monkeypatch.setattr(timeouts, "record_timeout", recorded)
    consumer = runtime_process.MailConsumer(drain_seconds=5, argv=MAIL_ARGV)
    return consumer, process, recorded, ages


def test_the_mail_consumer_reads_its_own_heartbeat_and_logs_as_mail(monkeypatch):
    """Late liveness is a durable mail_helper entry: the limit and how late."""
    from parishkit.stewardship.installer_health import MAIL_HEARTBEAT, MAX_AGE_SECONDS

    limit = runtime_process.MailConsumer.STALE_LIMIT
    consumer, _, recorded, ages = _consumer(
        monkeypatch, [MAX_AGE_SECONDS + 1, limit + 5]
    )
    consumer.check()
    with pytest.raises(ConfigError, match="mail consumer"):
        consumer.check()
    assert ages.call_args.args == (MAIL_HEARTBEAT,)
    warning, error = recorded.call_args_list
    assert warning.args[0].value == "helper_timed_out"
    assert warning.kwargs["what"] == error.kwargs["what"] == "mail_helper"
    assert warning.kwargs["level"] == "WARNING"
    assert warning.kwargs["limit_seconds"] == MAX_AGE_SECONDS
    assert error.kwargs["level"] == "ERROR"
    assert error.kwargs["limit_seconds"] == limit
    assert error.kwargs["elapsed_seconds"] == limit + 5


def test_an_exited_mail_consumer_stops_mail_dispatch_at_once(monkeypatch):
    """The container fails as a unit; exit is not a timeout."""
    consumer, process, recorded, _ = _consumer(monkeypatch, [])
    process.poll.return_value = 1
    with pytest.raises(ConfigError, match="mail consumer exited"):
        consumer.check()
    recorded.assert_not_called()


def test_a_mail_drain_past_the_grace_is_killed_and_logged(monkeypatch):
    """The kill at the end of the grace period is a logged mail_helper timeout."""
    consumer, process, recorded, _ = _consumer(monkeypatch, [])
    process.wait.side_effect = [subprocess.TimeoutExpired("x", 5), 0]
    consumer.close()
    process.kill.assert_called_once()
    assert recorded.call_args.kwargs["what"] == "mail_helper"
    assert recorded.call_args.kwargs["level"] == "ERROR"
    # The limit is the kill's own deadline: the grace less the kill margin
    # (none left of a five-second grace).
    assert recorded.call_args.kwargs["limit_seconds"] == 0


def test_only_mail_dispatch_runs_a_second_mail_consumer(tmp_path):
    """--queue mail on another role is refused before any assembly."""
    configuration = replace(configuration_at(tmp_path), service_role=ServiceRole.WORKER)
    with pytest.raises(ConfigError):
        runtime_process.serve_background(configuration, Mock(), mail=True)


def test_mail_consumers_default_to_two_and_can_fall_back_to_one(tmp_path):
    """YAML or the environment selects one; the rendered document keeps it."""
    from parishkit.stewardship.deployment_documents import deployment_document

    assert load_deployment(environ={}).mail_consumers == 2
    path = config_file(tmp_path, {"mail_consumers": 1})
    configuration = load_deployment(path, environ={})
    assert configuration.mail_consumers == 1
    (tmp_path / "rendered").mkdir()
    rendered = config_file(
        tmp_path / "rendered", deployment_document(configuration)["deployment"]
    )
    assert load_deployment(rendered, environ={}).mail_consumers == 1
    name = MAIL_CONSUMERS_VARIABLE
    assert load_deployment(path, environ={name: "2"}).mail_consumers == 2
    assert load_deployment(environ={name: "1"}).mail_consumers == 1
    # Compose passes the variable empty unless the operator exports it.
    assert load_deployment(rendered, environ={name: ""}).mail_consumers == 1
    assert load_deployment(environ={name: ""}).mail_consumers == 2
    with pytest.raises(ConfigError, match="mail_consumers"):
        load_deployment(environ={name: "3"})


@pytest.mark.parametrize("value", [0, 3, "2", True, 1.0, [1]])
def test_an_unreviewed_mail_consumer_count_is_rejected(tmp_path, value):
    """Only one or two: more would exceed the mail login's connection limit."""
    path = config_file(tmp_path, {"mail_consumers": value})
    with pytest.raises(ConfigError, match="mail_consumers"):
        load_deployment(path, environ={})


def test_only_mail_dispatch_receives_the_mail_consumer_switch(tmp_path):
    """The one-command fallback reaches the mail worker from the shell."""
    compose, _ = render_runtime(
        configuration_at(tmp_path, production=True), image=IMAGE
    )
    name = MAIL_CONSUMERS_VARIABLE
    services = compose["services"]
    assert services["mail-dispatch"]["environment"][name] == "${" + name + ":-}"
    assert [
        service
        for service, values in services.items()
        if name in values.get("environment", {})
    ] == ["mail-dispatch"]


def test_the_daily_count_is_reread_for_every_message_near_the_limit(monkeypatch):
    """Far from the limit the count is cached; near it, every message re-reads it.

    The count is the deployment's, from PostgreSQL, so near the limit one
    process sees what the other has already sent.
    """
    from parishkit.stewardship.jobs import family_mail_delivery_tasks as tasks

    counts = iter([10, 10, 1499, 1500, 1520, 1530, 1531])
    reads = Mock(side_effect=lambda: next(counts))
    monkeypatch.setattr(tasks, "sends_in_last_day", reads)
    clock = [1000.0]
    monkeypatch.setattr(tasks, "monotonic", lambda: clock[0])
    circuit = tasks.DeliveryCircuit()
    assert [circuit.daily_sends() for _ in range(5)] == [10] * 5
    assert reads.call_count == 1
    clock[0] += tasks.DAILY_COUNT_SECONDS
    assert circuit.daily_sends() == 10
    clock[0] += tasks.DAILY_COUNT_SECONDS
    assert circuit.daily_sends() == 1499
    clock[0] += tasks.DAILY_COUNT_SECONDS
    # 1500 is within NEAR_DAILY_LIMIT of the bulk limit (1600): from here on
    # each message re-reads the count, with no clock movement at all.
    assert [circuit.daily_sends() for _ in range(4)] == [1500, 1520, 1530, 1531]


@pytest.mark.parametrize("kind", ["SourceConsumer", "MailConsumer"])
def test_a_stopped_sibling_is_logged_and_killed_before_dockers_kill(monkeypatch, kind):
    """#370 M1: the drain wait is what is left of the grace, less a margin.

    Docker kills the container ``drain_seconds`` after its SIGTERM. The main
    process closes its sibling only after its own drain, so a full grace
    wait from then would always lose to Docker's kill and leave no entry.
    """
    from parishkit.stewardship.audit import timeouts

    clock = [1000.0]
    monkeypatch.setattr(runtime_process, "monotonic", lambda: clock[0])
    process = Mock(returncode=None)
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("x", 1), 0]
    monkeypatch.setattr(runtime_process.subprocess, "Popen", Mock(return_value=process))
    order = Mock()
    order.attach_mock(process.kill, "kill")
    order.attach_mock(Mock(), "record")
    monkeypatch.setattr(timeouts, "record_timeout", order.record)
    consumer = getattr(runtime_process, kind)(drain_seconds=360, argv=MAIL_ARGV)
    consumer.terminate()  # Docker's SIGTERM, forwarded by the signal handler.
    clock[0] += 200  # The main process's own drain.
    consumer.close()
    margin = runtime_process.SiblingConsumer.KILL_MARGIN
    assert process.wait.call_args_list[0].kwargs["timeout"] == 360 - margin - 200
    # The durable entry comes first, then the kill.
    assert [name for name, *_ in order.mock_calls] == ["record", "kill"]
    entry = order.record.call_args.kwargs
    assert entry["what"] == consumer.WHAT and entry["level"] == "ERROR"
    # The limit is the deadline it was killed at (the grace less the margin).
    assert entry["limit_seconds"] == 360 - margin
    assert entry["elapsed_seconds"] == 200


def test_a_main_drain_past_the_grace_leaves_no_wait_at_all(monkeypatch):
    """With the grace already spent, the sibling is logged and killed at once."""
    clock = [1000.0]
    monkeypatch.setattr(runtime_process, "monotonic", lambda: clock[0])
    consumer, process, recorded, _ = _consumer(monkeypatch, [])
    consumer.terminate()
    clock[0] += 400
    process.wait.side_effect = [subprocess.TimeoutExpired("x", 0), 0]
    consumer.close()
    assert process.wait.call_args_list[0].kwargs["timeout"] == 0
    process.kill.assert_called_once()
    assert recorded.call_args.kwargs["elapsed_seconds"] == 400


def test_a_systemic_stop_in_one_consumer_stops_the_other(tmp_path):
    """#370 L1: a configuration or credential fault stops the whole container."""
    from parishkit.stewardship.family_delivery import ProviderHealth
    from parishkit.stewardship.installer_health import clear_stopped
    from parishkit.stewardship.jobs.family_mail_delivery_tasks import DeliveryCircuit

    marker = tmp_path / "private" / "mail-systemic-stop"
    first, second = (
        DeliveryCircuit(recovery_seconds=600, systemic_stops=True, shared_stop=marker)
        for _ in range(2)
    )
    alone = DeliveryCircuit(recovery_seconds=600, systemic_stops=True)
    assert not first.blocks_new_send() and not second.blocks_new_send()
    assert first.observe(ProviderHealth.SYSTEMIC) is True
    assert marker.exists() and first.stopped
    assert second.blocks_new_send() is True and second.stopped
    # A stop is not an outage: no cooldown lifts it before a restart.
    second.recover_after = 0.0
    assert second.blocks_new_send() is True
    # A circuit with no shared marker (a test, the scheduler) is unaffected.
    assert alone.blocks_new_send() is False
    clear_stopped(marker)
    assert not marker.exists()
    clear_stopped(marker)  # Starting with no marker is the usual case.


def test_only_the_main_mail_process_clears_the_stop_marker(tmp_path, monkeypatch):
    """A restart lifts the stop; the sibling starts after the clearing."""
    from parishkit.stewardship import installer_health, runtime_background
    from parishkit.stewardship.jobs import processes

    marker = tmp_path / "mail-systemic-stop"
    monkeypatch.setattr(installer_health, "MAIL_SYSTEMIC_STOP", marker)
    beats, receipts, companions = [], Mock(), []
    monkeypatch.setattr(installer_health, "publish_heartbeat", beats.append)
    monkeypatch.setattr(
        "parishkit.stewardship.consumer_runtime.publish_single_process_receipts",
        receipts,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.credential_runtime.acknowledge_rotations", Mock()
    )

    class Unstarted(runtime_process.MailConsumer):
        """The real class, minus the child process."""

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def close(self):
            """Nothing to drain."""
            self.closed = True

    monkeypatch.setattr(runtime_process, "MailConsumer", Unstarted)
    selected = []

    def configure(config, *, stop, heartbeat, queues=None):
        """Record the queue choice and exercise the heartbeat."""
        selected.append(queues)
        heartbeat()
        return Mock(receipts={})

    def serve(broker, *, lease, stop, heartbeat, **kwargs):
        """Note the companion the process would supervise."""
        companions.append(kwargs.get("companion"))
        return 0

    monkeypatch.setattr(runtime_background, "configure_background", configure)
    monkeypatch.setattr(processes, "serve_consumer", serve)
    for mail in (True, False):
        marker.write_bytes(b"stopped")
        assert runtime_process.serve_background(_at(tmp_path), Mock(), mail=mail) == 0
        # The sibling leaves the marker; the main process removes it.
        assert marker.exists() is mail
    from parishkit.stewardship.installer_health import MAIL_HEARTBEAT

    # The sibling serves every mail queue, publishes its own heartbeat, and
    # neither publishes receipts nor starts a companion of its own.
    assert selected == [None, None]
    assert beats == [MAIL_HEARTBEAT, None]
    receipts.assert_called_once()
    assert companions[0] is None
    assert isinstance(companions[1], Unstarted) and companions[1].closed


def test_timeout_entries_are_written_one_at_a_time_per_process(monkeypatch):
    """#370 L3: one private timeout-log connection per process at most."""
    import threading
    import time

    from parishkit.stewardship.audit import timeouts
    from parishkit.stewardship.observability import Event

    active, peak, release = [0], [0], threading.Event()
    lock = threading.Lock()

    def slow_write(*args, **kwargs):
        """Hold the private connection until released."""
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        release.wait(5)
        with lock:
            active[0] -= 1

    monkeypatch.setattr(timeouts, "_write", slow_write)
    emitted = Mock()
    monkeypatch.setattr(timeouts, "emit", emitted)
    failures = Mock()
    monkeypatch.setattr(timeouts, "emit_failure", failures)
    monkeypatch.setattr(timeouts, "WRITER_WAIT_SECONDS", 0.2)
    writers = [
        threading.Thread(
            target=timeouts.record_timeout,
            args=(Event.HELPER_TIMED_OUT,),
            kwargs={"what": "mail_helper", "limit_seconds": 30, "elapsed_seconds": 31},
        )
        for _ in range(3)
    ]
    for writer in writers:
        writer.start()
    time.sleep(0.5)
    release.set()
    for writer in writers:
        writer.join(5)
    assert peak[0] == 1
    # The two that could not get the slot in time gave up without raising,
    # as a reviewed timeout of their own, not as an unexplained failure.
    failures.assert_not_called()
    entries = [c.kwargs for c in emitted.call_args_list]
    # Every entry's facts reached the process log before any durable write.
    facts = [e for e in entries if e.get("timeout") == "mail_helper"]
    assert len(facts) == 3
    assert all((e["limit_seconds"], e["elapsed_seconds"]) == (30, 31) for e in facts)
    gave_up = [e for e in entries if e.get("timeout") == "timeout_log_slot"]
    assert len(gave_up) == 2
    assert all(e["level"] == logging.WARNING for e in gave_up)
    assert all(e["limit_seconds"] == 0 for e in gave_up)  # 0.2 s, whole seconds.
    assert all(e["elapsed_seconds"] == 0 for e in gave_up)
    busy = [c.args[0] for c in emitted.call_args_list if c.kwargs in gave_up]
    assert busy == [Event.TASK_TIMED_OUT] * 2


def test_the_process_log_names_every_durable_timeout_kind():
    """#370 re-review M1: each durable ``what`` is a process-log timeout name.

    A timeout entry's facts go to the process log first, so they must be
    names the log formatter keeps.
    """
    import json
    import logging as logs

    from parishkit.stewardship.audit.schemas import TIMEOUT_KINDS
    from parishkit.stewardship.observability import (
        TIMEOUT_LIMITS,
        Event,
        SafeJsonFormatter,
        emit,
    )

    assert TIMEOUT_KINDS <= TIMEOUT_LIMITS
    records = []

    class Keep(logs.Handler):
        """Collect records instead of writing them."""

        def emit(self, record):
            records.append(record)

    keep = Keep()
    logger = logs.getLogger("parishkit.stewardship")
    logger.addHandler(keep)
    try:
        emit(
            Event.HELPER_TIMED_OUT,
            level=logs.ERROR,
            timeout="mail_helper",
            limit_seconds=345,
            elapsed_seconds=346,
        )
    finally:
        logger.removeHandler(keep)
    line = json.loads(SafeJsonFormatter().format(records[-1]))
    assert line["extra"] == {
        "timeout": "mail_helper",
        "limit_seconds": 345,
        "elapsed_seconds": 346,
    }


def test_a_stop_between_claim_and_effect_holds_without_spending_an_attempt(
    monkeypatch,
):
    """#370 re-review L2: a stop landing after the claim is a hold.

    The claimed message's effect admission raises FamilyDeliveryHeld, which
    the MAIL handler defers without charging its preparation budget; an
    unclaimed hint is simply refused, as before.
    """
    from types import SimpleNamespace

    from parishkit.stewardship.jobs import family_mail_delivery_tasks as tasks

    monkeypatch.setattr(
        tasks, "bound_dispatch", lambda status: SimpleNamespace(state="pending")
    )
    monkeypatch.setattr(tasks, "disposition", lambda row: None)
    stopped = SimpleNamespace(blocks_new_send=lambda: True)
    with pytest.raises(tasks.FamilyDeliveryHeld):
        tasks.admit_task("effect", Mock(), store=None, circuit=stopped)
    assert tasks.admit_task("claim", Mock(), store=None, circuit=stopped) is False
