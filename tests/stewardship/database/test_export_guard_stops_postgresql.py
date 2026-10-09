"""``stewardship_read_guard_kills_v1``: an export's read-guard stops (#386, L3).

The guard's own durable timeout entries are counted for one task, by the
general worker only, through the definer function; the worker still cannot
read the operational log's context directly.
"""

from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.audit.schemas import Outcome
from parishkit.stewardship.audit.timeouts import insert_timeout, timeout_context
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.observability import Event
from parishkit.stewardship.reports.export_tasks import guard_stops

from .test_background_grants_postgresql import task_login
from .test_export_jobs_postgresql import scenario  # noqa: F401 (fixture)
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def entry(task, what="read_guard"):
    """One durable timeout entry, as record_timeout writes it."""
    with connection.cursor() as cursor:
        insert_timeout(
            cursor,
            Event.TASK_TIMED_OUT,
            "ERROR",
            timeout_context(
                what=what,
                task_id=task,
                limit_seconds=300,
                elapsed_seconds=300,
                outcome=Outcome.FAILED,
            ),
        )


def test_only_this_tasks_read_guard_stops_are_counted():
    """Two guard stops for the task; another task's, and another kind of
    timeout for this task, are not counted."""
    task, other = uuid4(), uuid4()
    entry(task)
    entry(task)
    entry(other)
    entry(task, what="lock_timeout")
    with task_login(ServiceRole.WORKER):
        assert guard_stops(task) == 2
        assert guard_stops(other) == 1
        assert guard_stops(uuid4()) == 0
        # The worker still cannot read the context itself.
        with (
            pytest.raises(DatabaseError),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT context FROM stewardship_operational_log LIMIT 1")


def test_no_other_login_may_count():
    """EXECUTE is the worker's alone (database-grants), never PUBLIC's."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege('public', "
            "'public.stewardship_read_guard_kills_v1(uuid)', 'EXECUTE'), "
            "p.prosecdef FROM pg_proc p "
            "WHERE p.proname='stewardship_read_guard_kills_v1'"
        )
        assert cursor.fetchone() == (False, True)
    with (
        web_login(),
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT public.stewardship_read_guard_kills_v1(%s)", [uuid4()])


def test_a_login_with_execute_but_not_the_worker_is_refused_by_the_body():
    """The session_user check itself, apart from EXECUTE: a login granted
    EXECUTE here (web, within its own disposable role) is still refused,
    with the function's own SQLSTATE and message."""
    with web_login():
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(
                "GRANT EXECUTE ON FUNCTION "
                "public.stewardship_read_guard_kills_v1(uuid) TO pk_stewardship_web"
            )
            cursor.execute('SET SESSION AUTHORIZATION "pk_stewardship_web"')
        with (
            pytest.raises(DatabaseError) as caught,
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT public.stewardship_read_guard_kills_v1(%s)", [uuid4()]
            )
    cause = caught.value.__cause__
    assert cause.sqlstate == "42501"
    assert (
        cause.diag.message_primary == "Only the general worker counts read-guard stops"
    )


@pytest.fixture
def abandoned_export(scenario):  # noqa: F811
    """A real export request whose claim expired: (task id, handler)."""
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status
    from parishkit.stewardship.reports.export_tasks import export_handler

    from .test_export_jobs_postgresql import request_export
    from .test_taskrun_postgresql import act, expire

    store, _, _, root = scenario
    request = request_export(scenario)
    with work_transaction():
        claimed = act(
            _status(TaskRun.objects.get(pk=request.task_id)), "claim", lease_seconds=1
        )
    with work_transaction():
        expire(claimed)
    return request.task_id, export_handler(store=store, root=root)


@pytest.mark.parametrize(
    "stops,state", [(0, "retry_wait"), (1, "retry_wait"), (2, "failed")]
)
def test_recovery_end_to_end_counts_the_guard_stops(abandoned_export, stops, state):
    """recover_hint as the general worker: retried, retried after the longest
    delay once the guard stopped it, failed after a second stop."""
    from parishkit.stewardship.jobs.dispatch import recover_hint
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.ownership import database_now
    from parishkit.stewardship.jobs.queues import WorkQueue
    from parishkit.stewardship.reports.export_services import TASK_TYPE
    from parishkit.stewardship.reports.export_tasks import MAX_RETRY_SECONDS

    task, handler = abandoned_export
    for _ in range(stops):
        entry(task)
    with task_login(ServiceRole.WORKER):
        recover_hint(
            task,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: handler},
        )
    row = TaskRun.objects.get(pk=task)
    assert row.state == state
    if stops == 1:
        with transaction.atomic():
            waited = (row.not_before - database_now()).total_seconds()
        assert MAX_RETRY_SECONDS - 30 < waited <= MAX_RETRY_SECONDS
