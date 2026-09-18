"""Admission holds cannot poison independent scans or erase later failure budgets."""

from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint, execute_hint
from parishkit.stewardship.jobs.family_mail_delivery_tasks import preparation_attempts
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.operational_collection import (
    collection_handler,
    produce_collection,
)
from parishkit.stewardship.jobs.operational_fanout import fanout_handler
from parishkit.stewardship.jobs.outbox_dispatch import delivery_handler
from parishkit.stewardship.jobs.phases import TaskPhase
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scan_once, scheduler_session
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.observability import Event

from .test_background_grants_postgresql import task_login
from .test_operational_dispatch_postgresql import allocated
from .test_operational_fanout_postgresql import schedule
from .test_operational_routing_postgresql import routing as routing_fixture

routing = routing_fixture
pytestmark = pytest.mark.django_db(transaction=True)


def test_operational_mail_authority_hold_keeps_independent_scan_work(
    routing, monkeypatch
):
    """The actual composite MAIL owner cannot abort an independent collector hint."""
    store = routing[0]
    messages = allocated(routing)
    operational(Event.TASK_FAILED, level="CRITICAL")
    monkeypatch.setattr(store, "manifest_reference", lambda: (uuid4(), "f" * 64))
    hints = []
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        (collector,) = produce_collection(guard)
        result = scan_once(
            guard,
            handlers={
                "outbox_delivery": delivery_handler(store, scheduler=True),
                "operational_collect": collection_handler(scheduler=True),
            },
            publish=hints.append,
        )
    assert result.published == 1
    assert [hint.run_id for hint in hints] == [collector]
    assert all(
        TaskRun.objects.get(pk=row.outbox.task_id).attempt == 0 for row in messages
    )


@pytest.mark.parametrize("kind", ["mail", "fanout"])
def test_real_failure_after_a_hold_counts_again(routing, tmp_path, monkeypatch, kind):
    """An old RECONCILING phase is not proof that a later attempt was also held."""
    store = routing[0]
    if kind == "mail":
        identifier = allocated(routing)[0].outbox.task_id
        path = tmp_path / "wrong-workspace"
        write_private(path, b"synthetic-mismatched-key")
        owner = delivery_handler(store, credential_path=path)
        role, queue, task_type = (
            ServiceRole.MAIL_DISPATCH,
            WorkQueue.MAIL,
            "outbox_delivery",
        )
    else:
        (identifier,) = schedule()
        owner = fanout_handler(store)
        role, queue, task_type = (
            ServiceRole.WORKER,
            WorkQueue.GENERAL,
            "operational_prepare",
        )

        def failed_page(*args):
            """Represent an actual preparation fault rather than configuration loss."""
            raise ValueError("Synthetic non-configuration failure")

        monkeypatch.setattr(
            "parishkit.stewardship.jobs.operational_fanout.prepare_page", failed_page
        )
    handlers = {task_type: owner}
    with task_login(role, exact=True, reconnect=True):
        execution = claim_hint(
            identifier, queue=queue, worker_id=uuid4(), handlers=handlers
        )
        # Retain a legitimate held checkpoint using the compiled owner and real
        # journal. Only its retry delay is shortened, never a lease/provider clock.
        with maintain_execution(execution):
            execution.progress(0, 0, phase=TaskPhase.RECONCILING)
            execution.transition("retryable_failure", retry_seconds=1)
        with work_transaction():
            assert (
                preparation_attempts(_status(TaskRun.objects.get(pk=identifier))) == 0
            )
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(1.02)")
        if kind == "fanout":
            with pytest.raises(ValueError, match="non-configuration"):
                execute_hint(
                    identifier, queue=queue, worker_id=uuid4(), handlers=handlers
                )
        else:
            assert execute_hint(
                identifier, queue=queue, worker_id=uuid4(), handlers=handlers
            )
        with work_transaction():
            row = TaskRun.objects.get(pk=identifier)
            assert row.attempt == 2 and row.phase == "preparing"
            assert preparation_attempts(_status(row)) == 1
