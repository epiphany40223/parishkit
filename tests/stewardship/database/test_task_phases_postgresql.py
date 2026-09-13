"""Progress phases are typed, fenced, immutable in history and losslessly upgraded."""

import pytest
from django.db import IntegrityError, connection, transaction

from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.phases import TaskPhase

from .test_taskrun_postgresql import act, new

pytestmark = pytest.mark.django_db(transaction=True)


def test_phases_follow_claim_progress_and_survive_heartbeat_and_retry_history():
    """A retry resets its new attempt, without rewriting what the old one reached."""
    pending = new()
    assert pending.phase is TaskPhase.UNSPECIFIED
    running = act(pending, "claim")
    assert running.phase is TaskPhase.STARTING
    fetching = act(running, "progress", progress=(1, 3), phase=TaskPhase.FETCHING)
    assert fetching.phase is TaskPhase.FETCHING
    renewed = act(fetching, "heartbeat", lease_seconds=60)
    assert renewed.phase is TaskPhase.FETCHING
    waiting = act(renewed, "retryable_failure", retry_seconds=1)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(1.05)")
    restarted = act(waiting, "claim")
    assert restarted.phase is TaskPhase.STARTING
    row = TaskRun.objects.get(pk=restarted.run_id)
    assert list(row.events.order_by("version").values_list("phase", flat=True)) == [
        "unspecified",
        "starting",
        "fetching",
        "fetching",
        "fetching",
        "starting",
    ]


@pytest.mark.parametrize("phase", ["private-value", "unspecified", "promoting"])
def test_raw_heartbeat_cannot_change_the_recorded_phase(phase):
    """SQL rejects an unknown phase or one changed through the wrong action."""
    running = act(new(), "claim")
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_task_run SET phase=%s,action='heartbeat', "
            "version=version+1,actor_id=worker_id,heartbeat_at=statement_timestamp(), "
            "lease_expires_at=statement_timestamp()+interval '60 seconds' WHERE id=%s",
            [phase, running.run_id],
        )
    assert TaskRun.objects.get(pk=running.run_id).phase == "starting"


def test_phase_history_cannot_be_rewritten():
    """Even a valid known phase is not permission to change an old attempt record."""
    running = act(new(), "claim")
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "UPDATE stewardship_task_event SET phase='fetching' WHERE run_id=%s",
            [running.run_id],
        )
