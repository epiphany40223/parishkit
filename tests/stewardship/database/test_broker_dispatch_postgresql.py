"""App-local Celery task boundary exercises the actual durable dispatcher."""

import logging
from uuid import uuid4

import pytest

from parishkit.stewardship.deployment import ServiceRole, ValkeyConfiguration
from parishkit.stewardship.jobs.broker import HINT_TASK, build_broker, consume_hint
from parishkit.stewardship.jobs.dispatch import Handler, RecoveryPlan, WorkQueue
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.observability import Event, FailureKind

from .test_dispatch_postgresql import expired_task, queued

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


def test_general_process_claims_an_export_while_a_source_refresh_runs():
    """A short task completes while a long source task holds its claim (#336).

    The worker container's two processes each consume their own queues, so a
    general-queue task (an export stand-in) is claimed and finished by the
    general process while the source process is still inside a refresh.
    """
    from threading import Thread

    from django.db import connections

    from parishkit.stewardship.jobs.queues import ROLE_QUEUES, SOURCE_QUEUES
    from parishkit.stewardship.jobs.storage import enqueue

    source_task = queued()
    export_task = enqueue(
        task_type="dispatch_export_probe",
        domain_request_id=uuid4(),
        actor_id=None,
        correlation_id=uuid4(),
        admit=lambda *args: True,
    )
    events = []

    def export(context):
        """The short task: claimed and completed during the refresh."""
        events.append("export")
        context.transition("complete")

    handlers = {
        "dispatch_export_probe": Handler(WorkQueue.GENERAL, lambda *a: True, export)
    }

    def process(queues):
        """One of the worker container's two consumer processes."""
        return build_broker(
            endpoint=ValkeyConfiguration("valkey", 6379, 0, None),
            password="synthetic-password",
            service=ServiceRole.WORKER,
            handlers=handlers,
            queues=queues,
        )

    def run_general():
        """The general process's thread, on its own database connection."""
        try:
            general.app.tasks[HINT_TASK].run(str(export_task.run_id))
        finally:
            connections.close_all()

    def refresh(context):
        """The long task: mid-refresh, the export is claimed and finished."""
        events.append("refresh started")
        assert TaskRun.objects.get(pk=source_task.run_id).state == "running"
        thread = Thread(target=run_general)
        thread.start()
        thread.join(30)
        assert TaskRun.objects.get(pk=export_task.run_id).state == "succeeded"
        events.append("refresh finished")
        context.transition("complete")

    handlers["dispatch_probe"] = Handler(WorkQueue.SOURCE, lambda *a: True, refresh)
    # Each process's broker copies the registry when it is built.
    general = process(ROLE_QUEUES[ServiceRole.WORKER] - SOURCE_QUEUES)
    source = process(SOURCE_QUEUES)
    source.app.tasks[HINT_TASK].run(str(source_task.run_id))
    assert events == ["refresh started", "export", "refresh finished"]
    assert TaskRun.objects.get(pk=source_task.run_id).state == "succeeded"


@pytest.mark.parametrize("source_process", [False, True])
def test_each_worker_process_refuses_the_other_process_queue(source_process):
    """A hint for the other process's queue is refused and stays durable."""
    from parishkit.stewardship.jobs.queues import ROLE_QUEUES, SOURCE_QUEUES

    task, calls = queued(), []
    queue = WorkQueue.GENERAL if source_process else WorkQueue.SOURCE
    handler = Handler(queue, lambda *args: True, calls.append)
    with pytest.raises(PermissionError):
        consume_hint(
            (str(task.run_id),),
            {},
            service=ServiceRole.WORKER,
            handlers={"dispatch_probe": handler},
            queues=SOURCE_QUEUES
            if source_process
            else ROLE_QUEUES[ServiceRole.WORKER] - SOURCE_QUEUES,
        )
    assert not calls and TaskRun.objects.get(pk=task.run_id).state == "queued"


def refusal_lines(caplog, task, handler):
    """Consume one real hint for ``task``; its (level, failure_kind) lines (#1659)."""
    from parishkit.logging import STRUCTURED_EXTRA_FIELD

    runtime = build_broker(
        endpoint=ValkeyConfiguration("valkey", 6379, 0, None),
        password="synthetic-password",
        service=ServiceRole.WORKER,
        handlers={"dispatch_probe": handler},
    )
    with caplog.at_level(logging.INFO, logger="parishkit.stewardship"):
        runtime.app.tasks[HINT_TASK].run(str(task.run_id))
    return [
        (record.levelno, getattr(record, STRUCTURED_EXTRA_FIELD)["failure_kind"])
        for record in caplog.records
        if record.msg == Event.TASK_FAILED
    ]


def test_a_refused_effect_is_info_and_leaves_the_task_running(caplog):
    """A halt landing between claim and effect is routine; the lease recovers it."""

    def execute(context):
        """Reach the effect, which the handler now refuses."""
        with context.effect():
            pytest.fail("A refused effect must not run.")

    handler = Handler(
        WorkQueue.GENERAL, lambda action, status: action != "effect", execute
    )
    task = queued()
    lines = refusal_lines(caplog, task, handler)
    assert lines == [(logging.INFO, FailureKind.TASK_NOT_ADMITTED)]
    assert TaskRun.objects.get(pk=task.run_id).state == "running"


def test_a_refused_completion_is_still_an_error(caplog):
    """Completing against the handler's own evidence is a fault, not a hold."""
    handler = Handler(
        WorkQueue.GENERAL,
        lambda action, status: action != "complete",
        lambda context: context.transition("complete"),
    )
    task = queued()
    lines = refusal_lines(caplog, task, handler)
    assert lines == [(logging.ERROR, FailureKind.TASK_TRANSITION_REFUSED)]
    assert TaskRun.objects.get(pk=task.run_id).state == "running"


def test_a_refused_recovery_is_still_an_error(caplog):
    """A recovery plan the handler's admission refuses keeps its ERROR line."""
    handler = Handler(
        WorkQueue.GENERAL,
        lambda action, status: action != "recovery_retry",
        lambda context: None,
        recover=lambda status: RecoveryPlan("recovery_retry", 30),
    )
    task = expired_task()
    lines = refusal_lines(caplog, task, handler)
    assert lines == [(logging.ERROR, FailureKind.TASK_TRANSITION_REFUSED)]
    # The recovery pass is one transaction, so its lease expiry rolled back
    # with the refused plan: the expired run is left for the next pass.
    assert TaskRun.objects.get(pk=task.run_id).state == "running"
