"""Closed Celery/Valkey hint transport; PostgreSQL remains the task authority.

Configuration follows Celery 5.6's documented settings and the pinned Kombu
transport. This factory does not admit/start a service or read credential files;
the isolated runtime must first validate mounts, database grants and secrets.
"""

import os
from dataclasses import dataclass, field
from threading import Event
from types import MappingProxyType
from uuid import UUID, uuid4

from celery import Celery
from celery.loaders.base import BaseLoader
from kombu import Exchange, Queue
from kombu.exceptions import OperationalError

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import ServiceRole, ValkeyConfiguration, _host
from parishkit.stewardship.observability import emit_failure

from .dispatch import Handler, WorkQueue, execute_hint, recover_hint
from .models import TaskRun
from .queues import BROKER_PREFIX, HINT_TASK, ROLE_QUEUES
from .scanning import ExecutionHint
from .scheduler import HintPublicationUnavailable


class ClosedLoader(BaseLoader):
    """Never import ambient celeryconfig.py or an environment-selected module."""

    def read_configuration(self, *args, **kwargs):
        """Only the application factory's validated closed settings are loaded."""
        return {}


@dataclass(frozen=True)
class BrokerRuntime:
    """Keep app configuration/passwords and handler closures out of repr/logs."""

    app: Celery = field(repr=False)
    service: ServiceRole
    stop: Event | None = field(default=None, repr=False, compare=False)


def build_broker(*, endpoint, password, service, handlers, stop=None):
    """Construct a lazy authenticated broker client without a password-bearing URL."""
    if not isinstance(service, ServiceRole) or service not in ROLE_QUEUES:
        raise ConfigError("This service cannot access background queues.")
    if stop is not None and not isinstance(stop, Event):
        raise ConfigError("A process-owned worker stop event is required.")
    if not isinstance(endpoint, ValkeyConfiguration):
        raise ConfigError("Validated Valkey connection metadata is required.")
    host = _host(endpoint.host, "Valkey host")
    if (
        type(endpoint.port) is not int
        or not 1 <= endpoint.port <= 65535
        or type(endpoint.database) is not int
        or not 0 <= endpoint.database <= 15
    ):
        raise ConfigError("Valkey connection numbers are invalid.")
    if (
        type(password) is not str
        or not 1 <= len(password) <= 256
        or any(ord(char) <= 32 or ord(char) >= 127 for char in password)
    ):
        raise ConfigError("The individual Valkey password is invalid.")
    if any(name.startswith("CELERY_") for name in os.environ):
        raise ConfigError("Ambient Celery configuration overrides are not admitted.")
    registry = MappingProxyType(dict(handlers))
    if any(not isinstance(handler, Handler) for handler in registry.values()):
        raise ConfigError("A closed internal task registry is required.")
    app = Celery(
        "parishkit.stewardship", loader=ClosedLoader, fixups=[], set_as_current=False
    )
    queues = (
        frozenset(WorkQueue)
        if service is ServiceRole.SCHEDULER
        else ROLE_QUEUES[service]
    )
    default_queue = {
        ServiceRole.SCHEDULER: WorkQueue.GENERAL,
        ServiceRole.WORKER: WorkQueue.GENERAL,
        ServiceRole.MAIL_DISPATCH: WorkQueue.MAIL,
        ServiceRole.BACKUP_WORKER: WorkQueue.BACKUP,
    }[service]
    # Keep credentials as separate in-memory options, never URI components.
    app.conf.update(
        broker_host=host,
        broker_port=endpoint.port,
        broker_vhost=str(endpoint.database),
        broker_transport="redis",
        broker_user=service.value,
        broker_password=password,
        broker_connection_timeout=3,
        broker_pool_limit=1,
        broker_connection_retry_on_startup=False,
        broker_connection_retry=False,
        broker_transport_options={
            "global_keyprefix": BROKER_PREFIX,
            "socket_connect_timeout": 3,
            "socket_timeout": 3,
            "retry_on_timeout": False,
            "max_retries": 0,
            "visibility_timeout": 300,
            "unacked_key": f"{service.value}:unacked",
            "unacked_index_key": f"{service.value}:unacked_index",
            "unacked_mutex_key": f"{service.value}:unacked_mutex",
        },
        accept_content=["json"],
        result_accept_content=["json"],
        task_serializer="json",
        task_protocol=2,
        result_backend=None,
        task_ignore_result=True,
        task_store_errors_even_if_ignored=False,
        task_remote_tracebacks=False,
        task_track_started=False,
        task_publish_retry=False,
        task_acks_late=True,
        task_reject_on_worker_lost=False,
        task_create_missing_queues=False,
        task_default_queue=default_queue.value,
        task_queues=tuple(
            Queue(
                queue.value,
                Exchange(queue.value, type="direct"),
                routing_key=queue.value,
            )
            for queue in sorted(queues)
        ),
        worker_enable_remote_control=False,
        worker_send_task_events=False,
        task_send_sent_event=False,
        worker_prefetch_multiplier=1,
        worker_hijack_root_logger=False,
        enable_utc=True,
        timezone="UTC",
    )

    @app.task(
        name=HINT_TASK, ignore_result=True, typing=False, shared=False, lazy=False
    )
    def consume(*args, **kwargs):
        """Only a canonical UUID reaches dispatch; private errors stay local."""
        try:
            consume_hint(args, kwargs, service=service, handlers=registry, stop=stop)
        except Exception as error:
            emit_failure(error)
        finally:
            from django.db import connections

            connections.close_all()
        # No ORM objects, operational errors or business values enter a backend.
        return None

    app.finalize()
    # No canvas/control/helper task is part of this system's broker vocabulary.
    for name in tuple(app.tasks):
        if name != HINT_TASK:
            del app.tasks[name]
    return BrokerRuntime(app, service, stop)


def consume_hint(args, kwargs, *, service, handlers, stop=None):
    """Resolve service/queue from trusted startup and durable type, never headers."""
    if service not in ROLE_QUEUES or not ROLE_QUEUES[service]:
        raise PermissionError("This service is not an execution consumer.")
    if stop is not None:
        if not isinstance(stop, Event):
            raise ValueError("A process-owned worker stop event is required.")
        if stop.is_set():
            return False
    if kwargs or len(args) != 1 or type(args[0]) is not str or len(args[0]) != 36:
        raise ValueError("Execution hints contain only one canonical task UUID.")
    try:
        run_id = UUID(args[0])
    except ValueError:
        raise ValueError("Execution hints require a canonical task UUID.") from None
    if str(run_id) != args[0]:
        raise ValueError("Execution hints require a canonical task UUID.")
    task_type = (
        TaskRun.objects.filter(pk=run_id).values_list("task_type", flat=True).first()
    )
    if task_type is None:
        return False
    handler = handlers.get(task_type)
    if not isinstance(handler, Handler) or handler.queue not in ROLE_QUEUES[service]:
        raise PermissionError("Task type is unavailable to this isolated consumer.")
    options = dict(queue=handler.queue, worker_id=uuid4(), handlers=handlers)
    if execute_hint(run_id, **options, stop=stop):
        return True
    if stop is not None and stop.is_set():
        return False
    return recover_hint(run_id, **options)


def publish_hint(runtime, hint):
    """Publish one finite, UUID-only hint; any uncertain delivery is safe to repeat."""
    if (
        not isinstance(runtime, BrokerRuntime)
        or runtime.service is not ServiceRole.SCHEDULER
    ):
        raise PermissionError("Only the isolated scheduler publishes execution hints.")
    if (
        not isinstance(hint, ExecutionHint)
        or not isinstance(hint.run_id, UUID)
        or not isinstance(hint.queue, WorkQueue)
    ):
        raise ValueError("Only a canonical execution hint may be published.")
    try:
        runtime.app.send_task(
            HINT_TASK,
            args=(str(hint.run_id),),
            queue=hint.queue.value,
            routing_key=hint.queue.value,
            retry=False,
            ignore_result=True,
            expires=60,
        )
    except (OperationalError, OSError):
        raise HintPublicationUnavailable(
            "Execution hint publication is unavailable."
        ) from None
