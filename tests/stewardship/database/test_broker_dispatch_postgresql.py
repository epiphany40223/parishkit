"""App-local Celery task boundary exercises the actual durable dispatcher."""

from uuid import uuid4

import pytest

from parishkit.stewardship.deployment import ServiceRole, ValkeyConfiguration
from parishkit.stewardship.jobs.broker import HINT_TASK, build_broker, consume_hint
from parishkit.stewardship.jobs.dispatch import Handler, WorkQueue
from parishkit.stewardship.jobs.models import TaskRun

from .test_dispatch_postgresql import queued

pytestmark = pytest.mark.django_db(transaction=True)


def test_worker_task_consumes_only_the_durable_identity_and_deduplicates():
    """Celery receipt is not a separate execution or result record."""
    task, calls = queued(), []

    def execute(context):
        """Complete one synthetic task through its genuine fenced transaction."""
        calls.append(context.claim.run_id)
        context.transition("complete")

    runtime = build_broker(
        endpoint=ValkeyConfiguration("valkey", 6379, 0, None),
        password="synthetic-password",
        service=ServiceRole.WORKER,
        handlers={
            "dispatch_probe": Handler(WorkQueue.GENERAL, lambda *args: True, execute)
        },
    )
    consume = runtime.app.tasks[HINT_TASK]
    assert consume.run(str(task.run_id)) is None
    assert consume.run(str(task.run_id)) is None
    assert calls == [task.run_id]
    assert TaskRun.objects.get(pk=task.run_id).state == "succeeded"


@pytest.mark.parametrize(
    "args,kwargs",
    [
        ((), {}),
        (("bad",), {}),
        ((str(uuid4()), str(uuid4())), {}),
        ((str(uuid4()),), {"queue": "restore-mail"}),
        ((uuid4(),), {}),
    ],
)
def test_consumer_rejects_broker_supplied_options(args, kwargs):
    """Transport metadata cannot select provider payloads, queues or maintenance."""
    with pytest.raises(ValueError):
        consume_hint(args, kwargs, service=ServiceRole.WORKER, handlers={})


def test_general_worker_cannot_execute_a_durable_mail_task():
    """Even a real permitted task UUID cannot cross the provider-secret boundary."""
    task, calls = queued(), []
    handler = Handler(WorkQueue.MAIL, lambda *args: True, calls.append)
    with pytest.raises(PermissionError):
        consume_hint(
            (str(task.run_id),),
            {},
            service=ServiceRole.WORKER,
            handlers={"dispatch_probe": handler},
        )
    assert not calls and TaskRun.objects.get(pk=task.run_id).state == "queued"
