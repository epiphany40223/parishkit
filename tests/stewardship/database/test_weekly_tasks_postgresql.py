"""The maintained weekly worker completes finite preparation, never provider IO."""

from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.schedule_models import ScheduleOccurrence
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports import weekly_tasks
from parishkit.stewardship.reports.weekly_models import (
    WeeklyDigestPreparation,
    WeeklyDigestSnapshot,
)
from parishkit.stewardship.reports.weekly_ownership import TASK_TYPE

from .campaign_builders import campaign_clock, complete_empty_catchup
from .test_background_grants_postgresql import task_login
from .test_digest_schedule_planning_postgresql import add_digest
from .test_weekly_capture_postgresql import INSTANT, allocate
from .test_weekly_fanout_postgresql import configure_content, messages
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)


def queued(harness):
    """Allocate genuine configured work without claiming it outside dispatch."""
    if harness.campaign.state == "active":
        complete_empty_catchup(harness.campaign, uuid4())
    add_digest(harness.service.store, harness.campaign, weekly=True)
    configure_content(harness)
    return allocate(claim_task=False)


def execute(status):
    """Maintain the actual worker lifetime through detached rendering and effects."""
    owner = weekly_tasks.weekly_handler(public_origin="https://parish.example")
    execution = claim_hint(
        status.run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: owner},
    )
    with maintain_execution(execution):
        owner.execute(execution)


def test_actual_weekly_worker_completes_preparation_not_delivery(live_response_service):
    respond(live_response_service, "Maintained private weekly request")
    with campaign_clock(INSTANT):
        status = queued(live_response_service)
        with task_login(ServiceRole.WORKER, exact=True):
            execute(status)
        assert TaskRun.objects.get(pk=status.run_id).state == "succeeded"
        row = WeeklyDigestPreparation.objects.get(task_id=status.run_id)
        assert row.phase == "complete"
        assert ScheduleOccurrence.objects.get(pk=row.occurrence_id).state == "pending"
        message = messages().get()
        assert message.state == "pending" and message.attempt == 0


def test_actual_weekly_worker_records_empty_success(response_service):
    with campaign_clock(INSTANT):
        status = queued(response_service)
        with task_login(ServiceRole.WORKER, exact=True):
            execute(status)
        assert TaskRun.objects.get(pk=status.run_id).state == "succeeded"
        row = WeeklyDigestPreparation.objects.get(task_id=status.run_id)
        assert row.phase == "complete"
        assert ScheduleOccurrence.objects.get(pk=row.occurrence_id).state == "succeeded"
        assert not messages().exists()


def test_changed_page_reloads_without_failing_or_duplicate_allocation(
    live_response_service, monkeypatch
):
    respond(live_response_service, "Reload the changed coverage")
    retain = weekly_tasks.retain_weekly_page
    calls = []

    def competing_acceptance(*args):
        """Represent one newly accepted proof between detached compile and commit."""
        calls.append(None)
        if len(calls) == 1:
            raise weekly_tasks.WeeklyCoverageChanged("Synthetic coverage race")
        return retain(*args)

    monkeypatch.setattr(weekly_tasks, "retain_weekly_page", competing_acceptance)
    with campaign_clock(INSTANT):
        status = queued(live_response_service)
        with task_login(ServiceRole.WORKER, exact=True):
            execute(status)
        assert len(calls) == 2 and messages().count() == 1
        assert TaskRun.objects.get(pk=status.run_id).state == "succeeded"


def test_scheduler_can_admit_weekly_metadata_but_cannot_execute(response_service):
    with campaign_clock(INSTANT):
        status = queued(response_service)
        owner = weekly_tasks.weekly_handler(scheduler=True)
        with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
            assert owner.admit("hint", _status(TaskRun.objects.get(pk=status.run_id)))
            with pytest.raises(PermissionError, match="scheduler cannot prepare"):
                owner.execute(object())
        assert not WeeklyDigestSnapshot.objects.exists() and not messages().exists()
