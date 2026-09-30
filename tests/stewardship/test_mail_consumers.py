"""Mail dispatch runs two mail consumer processes by default.

One consumer sent Family mail one message at a time; the launch send took
the sum of every message's preparation and SMTP time. These tests pin the
second process's supervision (the #336 worker pattern), the connection budget
that admits it, the one-process fallback, and the shared daily count.
"""

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
    consumer = runtime_process.MailConsumer(drain_seconds=5, argv=MAIL_ARGV)
    command = popen.call_args.args[0]
    assert command[0] == runtime_process.sys.executable
    assert command[1:] == [*MAIL_ARGV[1:], "--queue", "mail"]
    consumer.check()  # Running and still inside its startup grace.
    consumer.close()
    process.send_signal.assert_called_once_with(signal.SIGTERM)
    process.wait.assert_called_once_with(timeout=5)
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
    assert recorded.call_args.kwargs["limit_seconds"] == 5


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
