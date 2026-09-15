"""Real scheduler ownership and durable boundary allocation, without broker IO."""

from datetime import timedelta

import pytest
from django.db import connection, transaction

from parishkit.stewardship.campaigns.boundary_production import (
    TASK_TYPE,
    produce_boundaries,
)
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import CampaignBoundaryOccurrence
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.scheduler import (
    SchedulerOwnershipLost,
    scheduler_session,
)
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import campaign_clock, command, draft_campaign, restored_runtime

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def scheduled(tmp_path):
    """Activate before start using the existing isolated readiness fixture."""
    _, campaign, actor = draft_campaign(tmp_path)
    with campaign_clock(campaign.active_configuration.starts_at - timedelta(days=1)):
        command(campaign, actor, Action.ACTIVATE)
    return campaign


@pytest.mark.parametrize(
    "boundary,offset,count",
    [
        ("starts_at", -1, 0),
        ("starts_at", 0, 1),
        ("starts_at", 1, 1),
        ("ends_at", -1, 1),
        ("ends_at", 0, 2),
        ("ends_at", 1, 2),
    ],
)
def test_exact_due_boundaries_materialize_without_changing_lifecycle(
    scheduled, boundary, offset, count
):
    """Both half-open boundaries use their stored resolved instants, not wall dates."""
    instant = getattr(scheduled.active_configuration, boundary) + timedelta(
        seconds=offset
    )
    with campaign_clock(instant), scheduler_session() as guard:
        result = produce_boundaries(guard)
    assert len(result) == CampaignBoundaryOccurrence.objects.count() == count
    assert TaskRun.objects.filter(task_type=TASK_TYPE).count() == count
    scheduled.refresh_from_db()
    assert scheduled.state == "scheduled"
    for item in result:
        row = CampaignBoundaryOccurrence.objects.get(pk=item.occurrence_id)
        task = TaskRun.objects.get(pk=item.task_root_id)
        assert row.state == "pending" and row.task_id is None and row.task_fence is None
        assert task.idempotency_key == str(row.pk)
        assert task.domain_request_id == scheduled.pk and task.state == "queued"


def test_repeated_scans_and_restart_reuse_roots_without_requiring_hints(scheduled):
    """A missing broker never loses the committed producer identity."""
    with campaign_clock(scheduled.active_configuration.ends_at):
        with scheduler_session() as guard:
            raw = connection.connection
            first = produce_boundaries(guard)
            assert produce_boundaries(guard) == first
            assert connection.connection is raw
        with scheduler_session() as guard:
            assert produce_boundaries(guard) == first
    assert len(first) == 2
    assert TaskRun.objects.filter(task_type=TASK_TYPE).count() == 2


def test_missing_configuration_and_testing_draft_do_not_consume_boundaries(tmp_path):
    """A due date is not permission to execute unconfigured or Testing work."""
    with scheduler_session() as guard:
        assert produce_boundaries(guard) == ()
    _, draft, _ = draft_campaign(tmp_path)
    with (
        campaign_clock(draft.active_configuration.ends_at),
        scheduler_session() as guard,
    ):
        assert produce_boundaries(guard) == ()
    assert not CampaignBoundaryOccurrence.objects.exists()


def test_restore_hold_does_not_consume_due_work(scheduled):
    """Release rescans durable campaign state; ordinary work has no exemption."""
    with (
        campaign_clock(scheduled.active_configuration.ends_at),
        restored_runtime(scheduled.active_configuration.starts_at),
        scheduler_session() as guard,
    ):
        assert produce_boundaries(guard) == ()
    assert not CampaignBoundaryOccurrence.objects.exists()


def test_producer_requires_its_own_transaction_and_live_scheduler(scheduled):
    """Neither a duck-typed callback nor a released scheduler can allocate work."""
    with pytest.raises(TypeError, match="scheduler ownership"):
        produce_boundaries(object())
    with (
        scheduler_session() as guard,
        transaction.atomic(),
        pytest.raises(StorageInvariantError),
    ):
        produce_boundaries(guard)
    with pytest.raises(SchedulerOwnershipLost):
        produce_boundaries(guard)
    assert not CampaignBoundaryOccurrence.objects.exists()
