"""Catch-up hints bind exact activation roots and independently retained holds."""

from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.catchup_tasks import (
    TASK_TYPE,
    admit_catchup,
    catchup_handler,
)
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import ActivationCatchUpDemand
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint, recover_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scanning import collect_hints
from parishkit.stewardship.jobs.storage import _status, enqueue

from .campaign_builders import (
    campaign_clock,
    command,
    complete_empty_catchup,
    draft_campaign,
)
from .test_background_grants_postgresql import task_login
from .test_taskrun_postgresql import act, expire

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def activated(tmp_path):
    """Use real lifecycle allocation without pretending preparation is complete."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at):
        command(campaign, actor, Action.ACTIVATE)
        yield campaign, actor, ActivationCatchUpDemand.objects.get()


def test_forged_stale_and_unbound_task_metadata_are_rejected(activated):
    """An arbitrary queue task cannot borrow the genuine demand's identity."""
    _, _, demand = activated
    status = _status(TaskRun.objects.get(pk=demand.task_root_id))
    with work_transaction():
        assert admit_catchup("hint", status)
        for changed in (
            replace(status, version=status.version + 1),
            replace(status, domain_request_id=uuid4()),
            replace(status, task_type="source_refresh"),
        ):
            with pytest.raises(PermissionError, match="ownership"):
                admit_catchup("hint", changed)
        unbound = enqueue(
            task_type=TASK_TYPE,
            domain_request_id=demand.pk,
            actor_id=None,
            correlation_id=uuid4(),
            idempotency_key=uuid4(),
            admit=lambda *args: True,
        )
        with pytest.raises(PermissionError, match="binding"):
            admit_catchup("hint", unbound)
        assert not admit_catchup("complete", status)
        assert not admit_catchup("safe_cancel", status)


def test_scheduler_scans_original_task_and_has_no_execution_port(activated):
    """Lost activation hints are recovered through the ordinary task scanner."""
    _, _, demand = activated
    handler = catchup_handler(scheduler=True)
    with task_login(ServiceRole.SCHEDULER):
        hints, _ = collect_hints(handlers={TASK_TYPE: handler})
        assert [hint.run_id for hint in hints] == [demand.task_root_id]
        with pytest.raises(PermissionError, match="cannot execute"):
            handler.execute(None)


def test_real_worker_claim_does_not_release_preparation_hold(activated):
    """Transport ownership and durable preparation completion remain distinct."""
    _, _, demand = activated
    with task_login(ServiceRole.WORKER):
        execution = claim_hint(
            demand.task_root_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: catchup_handler()},
        )
        assert execution is not None
        with work_transaction():
            status = _status(TaskRun.objects.get(pk=demand.task_root_id))
            assert not admit_catchup("complete", status)
    demand.refresh_from_db()
    assert demand.completed_at is None


@pytest.mark.parametrize("finished", [False, True])
def test_abandoned_task_recovers_from_checkpoint_not_task_terminality(
    activated, finished
):
    """A final checkpoint can be acknowledged after a crash; unfinished work retries."""
    campaign, actor, demand = activated
    if finished:
        complete_empty_catchup(campaign, actor)
    else:
        assert claim_hint(
            demand.task_root_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: catchup_handler()},
        )
    status = _status(TaskRun.objects.get(pk=demand.task_root_id))
    expire(act(status, "heartbeat", lease_seconds=1))
    with task_login(ServiceRole.WORKER):
        assert recover_hint(
            demand.task_root_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: catchup_handler()},
        )
    assert TaskRun.objects.get(pk=demand.task_root_id).state == (
        "succeeded" if finished else "retry_wait"
    )
    demand.refresh_from_db()
    assert (demand.completed_at is not None) == finished
    assert demand.cursor == ("end" if finished else "")


def test_scheduler_flag_requires_boolean():
    """A loosely coerced startup setting cannot select executable authority."""
    with pytest.raises(TypeError):
        catchup_handler(scheduler="false")
