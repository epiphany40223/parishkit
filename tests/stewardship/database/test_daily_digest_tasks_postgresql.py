"""The real worker completes daily preparation before any provider work exists."""

from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports.digest_models import (
    DailyDigestPreparation,
    DailyDigestSnapshot,
)
from parishkit.stewardship.reports.digest_ownership import TASK_TYPE
from parishkit.stewardship.reports.digest_tasks import daily_handler
from parishkit.stewardship.reports.facts import FactUnavailable

from .campaign_builders import campaign_clock, change
from .test_background_grants_postgresql import task_login
from .test_daily_digest_fanout_postgresql import configure_content
from .test_daily_digest_planning_postgresql import INSTANT, allocate
from .test_digest_schedule_planning_postgresql import add_digest
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def queued(harness):
    """Use real installed content and scheduler admission, without claiming work."""
    add_digest(harness.service.store, harness.campaign)
    configure_content(harness)
    return allocate(claim_task=False)


def execute(status, owner):
    """Maintain an actual dispatched lease through every private effect boundary."""
    execution = claim_hint(
        status.run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: owner},
    )
    with maintain_execution(execution):
        owner.execute(execution)


def test_worker_finishes_complete_preparation_and_not_delivery(family_mail):  # noqa: F811
    with campaign_clock(INSTANT):
        status = queued(family_mail)
        with task_login(ServiceRole.WORKER, exact=True):
            execute(status, daily_handler(public_origin="https://parish.example"))
        assert TaskRun.objects.get(pk=status.run_id).state == "succeeded"
        assert (
            DailyDigestPreparation.objects.get(task_id=status.run_id).phase
            == "complete"
        )
        message = OutboxMessage.objects.get()
        assert message.state == "pending" and message.attempt == 0
        assert TaskRun.objects.get(pk=message.task_id).state == "queued"


def test_waiting_exact_facts_preserves_snapshot_and_retries(family_mail, monkeypatch):  # noqa: F811
    def busy(*args):
        """Represent an independently owned builder without fabricating its data."""
        raise FactUnavailable("Synthetic generation dependency")

    monkeypatch.setattr(
        "parishkit.stewardship.reports.digest_tasks.begin_daily_facts", busy
    )
    with campaign_clock(INSTANT):
        status = queued(family_mail)
        with task_login(ServiceRole.WORKER, exact=True):
            execute(status, daily_handler(public_origin="https://parish.example"))
        task = TaskRun.objects.get(pk=status.run_id)
        assert task.state == "retry_wait"
        assert task.phase == "rendering"
        assert DailyDigestSnapshot.objects.count() == 1
        assert not OutboxMessage.objects.exists()


def test_scheduler_can_admit_metadata_but_cannot_execute(family_mail):  # noqa: F811
    with campaign_clock(INSTANT):
        status = queued(family_mail)
        handler = daily_handler(scheduler=True)
        with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
            assert handler.admit("hint", _status(TaskRun.objects.get(pk=status.run_id)))
            with pytest.raises(PermissionError, match="scheduler cannot prepare"):
                handler.execute(object())
        assert not DailyDigestSnapshot.objects.exists()


def test_queued_preparation_survives_unrelated_configuration_change(family_mail):  # noqa: F811
    """Freeze actual current inputs at generation, not the queue allocation date."""
    with campaign_clock(INSTANT):
        status = queued(family_mail)
        row = DailyDigestPreparation.objects.get(task_id=status.run_id)
        store = family_mail.service.store
        parish = store.active().document()["sections"]["parish"][0]
        assert (
            change(
                store,
                store.active(),
                uuid4(),
                [
                    {
                        "operation": "update",
                        "section": "parish",
                        "id": parish["id"],
                        "values": {"name": "Current Example Parish"},
                    }
                ],
            ).state
            == "applied"
        )
        with task_login(ServiceRole.WORKER, exact=True):
            execute(status, daily_handler(public_origin="https://parish.example"))
        snapshot = DailyDigestSnapshot.objects.get(preparation=row)
        assert snapshot.timezone_configuration_id != row.campaign_configuration_id
        assert snapshot.configuration_id == store.active().version_id
        assert TaskRun.objects.get(pk=status.run_id).state == "succeeded"
