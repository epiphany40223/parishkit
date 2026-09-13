"""Closed broker settings and producer contracts without a real Valkey server."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from kombu.exceptions import OperationalError

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import ServiceRole, ValkeyConfiguration
from parishkit.stewardship.jobs import broker as broker_module
from parishkit.stewardship.jobs.broker import HINT_TASK, build_broker, publish_hint
from parishkit.stewardship.jobs.dispatch import WorkQueue
from parishkit.stewardship.jobs.scanning import ExecutionHint
from parishkit.stewardship.jobs.scheduler import HintPublicationUnavailable


def broker(service=ServiceRole.SCHEDULER):
    """Construct a lazy client with a synthetic password and no network operations."""
    return build_broker(
        endpoint=ValkeyConfiguration("valkey", 6379, 0, None),
        password="synthetic-private-password",
        service=service,
        handlers={},
    )


def test_factory_closes_task_and_serializer_vocabulary_and_keeps_password_out_of_url():
    """The application contains only the one reviewed hint task and JSON serializer."""
    runtime = broker()
    assert set(runtime.app.tasks) == {HINT_TASK}
    assert runtime.app.conf.accept_content == ["json"]
    assert not runtime.app.conf.worker_enable_remote_control
    assert not runtime.app.conf.task_create_missing_queues
    assert runtime.app.conf.result_backend is None
    assert "synthetic-private-password" not in repr(runtime)
    assert "synthetic-private-password" not in runtime.app.conf.broker_url
    connection = runtime.app.connection_for_write()
    assert (
        connection.userid == "scheduler"
        and connection.password == "synthetic-private-password"
    )
    assert connection.transport_options["socket_timeout"] == 3


def test_ambient_broker_override_fails_closed(monkeypatch):
    """Environment-selected endpoints cannot override isolated deployment metadata."""
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://untrusted.invalid")
    with pytest.raises(ConfigError, match="Ambient"):
        broker()


def test_only_scheduler_publishes_uuid_without_provider_arguments():
    """General workers cannot use this API as an out-of-band provider queue."""
    runtime, hint = broker(), ExecutionHint(uuid4(), WorkQueue.GENERAL)
    runtime.app.send_task = Mock()
    publish_hint(runtime, hint)
    arguments, options = runtime.app.send_task.call_args
    assert arguments == (HINT_TASK,) and options["args"] == (str(hint.run_id),)
    assert options["retry"] is False and options["expires"] == 60
    with pytest.raises(PermissionError):
        publish_hint(broker(ServiceRole.WORKER), hint)


def test_transport_error_has_no_private_values_and_preserves_unknown_acceptance():
    """Known producer exceptions become a safe retryable hint-not-business outcome."""
    runtime = broker()
    runtime.app.send_task = Mock(
        side_effect=OperationalError("private connection details")
    )
    with pytest.raises(HintPublicationUnavailable) as error:
        publish_hint(runtime, ExecutionHint(uuid4(), WorkQueue.GENERAL))
    assert "private connection" not in str(error.value)


def test_web_profile_never_obtains_a_background_transport():
    """The existing web-only limiter identity remains separate from queue access."""
    with pytest.raises(ConfigError, match="cannot access"):
        broker(ServiceRole.WEB)


def test_application_instances_never_share_another_services_handler_closure(
    monkeypatch,
):
    """Celery shared-task defaults must not bind future apps to a prior service."""
    calls = []

    def consume(args, kwargs, *, service, handlers, stop):
        """Capture only the service identity passed by each app-local closure."""
        calls.append(service)

    monkeypatch.setattr(broker_module, "consume_hint", consume)
    producer, consumer = broker(), broker(ServiceRole.WORKER)
    producer.app.tasks[HINT_TASK].run(str(uuid4()))
    consumer.app.tasks[HINT_TASK].run(str(uuid4()))
    assert calls == [ServiceRole.SCHEDULER, ServiceRole.WORKER]


@pytest.mark.parametrize(
    "service,expected",
    [
        (ServiceRole.WORKER, {"general", "restore-general"}),
        (ServiceRole.MAIL_DISPATCH, {"mail-dispatch", "restore-mail"}),
        (ServiceRole.BACKUP_WORKER, {"backup-worker", "restore-backup"}),
    ],
)
def test_worker_default_queues_and_unacked_keys_are_service_isolated(service, expected):
    """Default startup cannot steal another service's hints or retry inventory."""
    runtime = broker(service)
    assert {queue.name for queue in runtime.app.conf.task_queues} == expected
    assert runtime.app.conf.task_default_queue in expected
    options = runtime.app.conf.broker_transport_options
    for name in ("unacked_key", "unacked_index_key", "unacked_mutex_key"):
        assert options[name].startswith(service.value + ":")
