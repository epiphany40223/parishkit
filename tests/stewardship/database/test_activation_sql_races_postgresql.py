"""Direct restricted connections cannot race past the single-preparation owner."""

# ruff: noqa: F811 -- imported fixture dependencies are injected by pytest name.

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from queue import Queue
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.campaigns.activation_models import ProductionTokenPreparation
from parishkit.stewardship.campaigns.activation_tokens import current_inputs
from parishkit.stewardship.campaigns.production_models import (
    ProductionTransitionRequest,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import _status

from .test_activation_views_postgresql import (  # noqa: F401
    bootstrapped,
    config_role,
    ready_cleanup,
    ready_links,
    setup_service,
)
from .test_setup_mail_views_postgresql import web_login
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


def raw_preparation(transition, inputs, actor):
    """Deliberately omit Python admission/work ordering and exercise SQL guards."""
    identifier, task_id, correlation = uuid4(), uuid4(), uuid4()
    TaskRun.objects.create(
        id=task_id,
        root_id=task_id,
        task_type="production_tokens",
        domain_request_id=identifier,
        idempotency_key=str(identifier),
        initiated_by_id=actor,
        actor_id=actor,
        correlation_id=correlation,
    )
    return ProductionTokenPreparation.objects.create(
        id=identifier,
        transition_id=transition.pk,
        task_id=task_id,
        request_key=uuid4(),
        actor_id=actor,
        correlation_id=correlation,
        **asdict(inputs),
    )


def competing(operation, started):
    """Use a distinct real web login connection and close it before dropping roles."""
    connection.close()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_web")
            cursor.execute("SET statement_timeout='5s'")
            cursor.execute("SELECT pg_backend_pid()")
            started.put(cursor.fetchone()[0])
        with transaction.atomic():
            operation()
        return "committed"
    except DatabaseError as error:
        return error.__cause__.sqlstate
    finally:
        connection.close()


def wait_for_work_lock(pid):
    """Observe an actual advisory wait, never infer serialization from a sleep."""
    deadline = monotonic() + 3
    while monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=%s "
                "AND locktype='advisory' AND classid=736220 AND objid=1 "
                "AND NOT granted)",
                [pid],
            )
            if cursor.fetchone()[0]:
                return
        Event().wait(0.01)
    pytest.fail("Competing intake never reached the serialized SQL guard")


@pytest.mark.parametrize("retry", [False, True])
def test_direct_intake_and_retry_cannot_commit_competing_preparations(
    ready_links, retry
):
    """Intake waits for fresh committed truth; un-ordered retry fails before waiting."""
    _, _, _, login = ready_links
    transition = ProductionTransitionRequest.objects.get(state="cleanup_complete")
    actor = login.portal_session.principal_id
    with work_transaction():
        inputs = current_inputs(transition)
    if retry:
        with web_login(), work_transaction():
            prior = raw_preparation(transition, inputs, actor)
        act(
            act(_status(TaskRun.objects.get(pk=prior.task_id)), "claim"),
            "permanent_failure",
        )

        def operation():
            """A raw child insert has no Python retry owner's lock-order protection."""
            TaskRun.objects.create(
                root_id=prior.task_id,
                parent_id=prior.task_id,
                retry_sequence=1,
                retry_command_id=uuid4(),
                task_type="production_tokens",
                domain_request_id=prior.pk,
                initiated_by_id=actor,
                actor_id=actor,
                correlation_id=uuid4(),
                action="explicit_retry",
                idempotency_key=f"retry:{prior.task_id}:1",
            )
    else:

        def operation():
            """The competing INSERT begins while the first intake is uncommitted."""
            raw_preparation(transition, inputs, actor)

    started = Queue()
    with web_login(), ThreadPoolExecutor(max_workers=1) as pool:
        with work_transaction():
            winner = raw_preparation(transition, inputs, actor)
            future = pool.submit(competing, operation, started)
            pid = started.get(timeout=3)
            if retry:
                # A late lock acquisition here would invert work/root order.
                # Reject the missing pre-held work lock without waiting on us.
                assert future.result(timeout=3) == "42501"
            else:
                wait_for_work_lock(pid)
        if not retry:
            assert future.result(timeout=5) == "23514"
    assert ProductionTokenPreparation.objects.count() == (2 if retry else 1)
    assert (
        TaskRun.objects.filter(task_type="production_tokens", state="queued").get().pk
        == winner.task_id
    )
