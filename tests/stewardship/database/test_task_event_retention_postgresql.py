"""``stewardship_task_event_prune_v1`` and its guard (#386, L2).

Real runs are claimed, heartbeated, progressed and completed through the
dispatcher, so their events are the ones production writes; a run's end is
moved into the past with replication-role updates (test superuser only).
"""

from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import Handler, WorkQueue, execute_hint
from parishkit.stewardship.jobs.models import TaskRunEvent

from .test_background_grants_postgresql import task_login
from .test_dispatch_postgresql import queued
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def finished_run(*, complete=True, end="complete"):
    """One run with a claim, two heartbeats, a progress event and, unless
    ``complete`` is false, its ending transition ``end``; returns its id."""
    task = queued()

    def execute(context):
        context.heartbeat()
        context.heartbeat()
        context.progress(1, 2)
        if complete:
            options = {"retry_seconds": 1} if end == "retryable_failure" else {}
            context.transition(end, **options)

    execute_hint(
        task.run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={
            "dispatch_probe": Handler(WorkQueue.GENERAL, lambda *a: True, execute)
        },
    )
    return task.run_id


def age(run_id, days):
    """Move a run's last update ``days`` into the past, bypassing its guards."""
    with connection.cursor() as cursor:
        cursor.execute("SET session_replication_role = replica")
        try:
            cursor.execute(
                "UPDATE stewardship_task_run SET updated_at=statement_timestamp()"
                "-make_interval(days=>%s) WHERE id=%s",
                [days, run_id],
            )
        finally:
            cursor.execute("SET session_replication_role = DEFAULT")


def actions(run_id):
    """The run's remaining event actions, in order."""
    return list(
        TaskRunEvent.objects.filter(run_id=run_id)
        .order_by("version")
        .values_list("action", flat=True)
    )


def prune(days=30, window=7, runs=20):
    """Call the function as the general worker; return its count."""
    with task_login(ServiceRole.WORKER), connection.cursor() as cursor:
        cursor.execute(
            "SELECT public.stewardship_task_event_prune_v1(%s,%s,%s)",
            [days, window, runs],
        )
        return cursor.fetchone()[0]


def test_only_old_finished_runs_lose_only_their_liveness_events():
    """An old finished run keeps its claim and completion; a recent one and
    an old unfinished one keep everything."""
    old, recent, unfinished = (
        finished_run(),
        finished_run(),
        finished_run(complete=False),
    )
    age(old, 31)
    age(unfinished, 31)
    before = {run: actions(run) for run in (old, recent, unfinished)}
    assert {"heartbeat", "progress"} <= set(before[old])
    assert prune() == before[old].count("heartbeat") + before[old].count("progress")
    assert actions(old) == [
        action for action in before[old] if action not in {"heartbeat", "progress"}
    ]
    assert actions(recent) == before[recent]
    assert actions(unfinished) == before[unfinished]
    assert prune() == 0


def test_batches_are_bounded_by_runs_and_the_window():
    """A batch of one run removes that run's liveness events only; a run
    older than the window is not looked at."""
    first, second, ancient = finished_run(), finished_run(), finished_run()
    age(first, 32)
    age(second, 33)
    age(ancient, 60)
    liveness = {"heartbeat", "progress"}
    assert prune(runs=1) == 3
    left = [run for run in (first, second) if liveness & set(actions(run))]
    assert len(left) == 1
    assert prune(runs=1) == 3 and prune() == 0
    assert liveness & set(actions(ancient))
    # A wider window, as an operator would run after a long outage.
    assert prune(window=60) == 3


@pytest.mark.parametrize("end", ["complete", "permanent_failure", "safe_cancel"])
def test_every_terminal_state_is_pruned(end):
    """Succeeded, failed and cancelled runs all lose their liveness events."""
    run = finished_run(end=end)
    age(run, 31)
    assert prune() == 3


def test_unfinished_runs_waiting_to_retry_are_kept():
    """A run waiting to retry is not finished, however old."""
    run = finished_run(end="retryable_failure")
    age(run, 40)
    before = actions(run)
    assert prune() == 0 and actions(run) == before


def test_the_cutoff_boundary():
    """Just inside the retention is kept; just past it is pruned."""
    inside, past = finished_run(), finished_run()
    with connection.cursor() as cursor:
        cursor.execute("SET session_replication_role = replica")
        try:
            for run, offset in ((inside, "-1 hour"), (past, "1 hour")):
                cursor.execute(
                    "UPDATE stewardship_task_run SET updated_at="
                    "statement_timestamp()-interval '30 days'-%s::interval "
                    "WHERE id=%s",
                    [offset, run],
                )
        finally:
            cursor.execute("SET session_replication_role = DEFAULT")
    assert prune() == 3
    assert "heartbeat" in actions(inside) and "heartbeat" not in actions(past)


def test_the_flag_alone_lets_no_other_login_delete():
    """Even with DELETE granted and the flag set, a login that is not the
    schema owner is refused by the guard itself."""
    run = finished_run()
    age(run, 40)
    event = TaskRunEvent.objects.filter(run_id=run, action="heartbeat").first()
    with web_login():
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            cursor.execute(
                "GRANT SELECT, DELETE ON stewardship_task_event TO pk_stewardship_web"
            )
            cursor.execute('SET SESSION AUTHORIZATION "pk_stewardship_web"')
        with (
            pytest.raises(DatabaseError, match="append-only"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute("SET LOCAL stewardship.event_prune = 'on'")
            cursor.execute("DELETE FROM stewardship_task_event WHERE id=%s", [event.pk])
    assert TaskRunEvent.objects.filter(pk=event.pk).exists()


@pytest.mark.parametrize(
    "days,window,runs",
    [(6, 7, 10), (30, 0, 10), (30, 3651, 10), (30, 7, 0), (30, 7, 201), (None, 7, 10)],
)
def test_out_of_range_arguments_are_refused(days, window, runs):
    """Fewer than 7 days, a window outside 1 to 3,650 days or a batch outside
    1 to 200 runs is refused."""
    with (
        task_login(ServiceRole.WORKER),
        pytest.raises(DatabaseError, match="at least 7 days"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT public.stewardship_task_event_prune_v1(%s,%s,%s)",
            [days, window, runs],
        )


def test_every_other_change_is_still_refused():
    """A direct DELETE or UPDATE, even of an old run's heartbeat, is refused,
    and the web login may not call the function."""
    run = finished_run()
    age(run, 40)
    event = TaskRunEvent.objects.filter(run_id=run, action="heartbeat").first()
    for statement in (
        "DELETE FROM stewardship_task_event WHERE id=%s",
        "UPDATE stewardship_task_event SET phase=phase WHERE id=%s",
    ):
        with (
            pytest.raises(DatabaseError, match="append-only"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, [event.pk])
    with (
        web_login(),
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT public.stewardship_task_event_prune_v1(30, 7, 10)")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_function_privilege('public', "
            "'public.stewardship_task_event_prune_v1(integer, integer, integer)', "
            "'EXECUTE')"
        )
        assert cursor.fetchone() == (False,)
    assert TaskRunEvent.objects.filter(pk=event.pk).exists()
