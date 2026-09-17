"""Late accepted observations finish through a fresh metadata-only claim."""

from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.family_delivery import FamilyDeliveryResult
from parishkit.stewardship.family_delivery import FamilyDeliveryStatus as Status
from parishkit.stewardship.jobs.delivery_states import DeliveryAction
from parishkit.stewardship.jobs.dispatch import claim_hint, execute_hint, recover_hint
from parishkit.stewardship.jobs.family_mail_dispatch import finish_submission
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.outbox_storage import _status as message_status
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports.digest_finalization import (
    TASK_TYPE,
    DailyDigestFinalizeProducer,
    finalization_handler,
)

from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_daily_digest_dispatch_postgresql import allocated, begin
from .test_daily_digest_planning_postgresql import INSTANT
from .test_family_mail_dispatch_postgresql import claim
from .test_family_mail_preparation_postgresql import family_mail  # noqa: F401
from .test_outbox_postgresql import change, provider_evidence
from .test_taskrun_postgresql import act, expire

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("crash_after_claim", [False, True])
def test_late_acceptance_does_not_rewrite_failed_provider_task(
    family_mail,  # noqa: F811
    crash_after_claim,
):
    with campaign_clock(INSTANT):
        ready = allocated(family_mail)
        message = OutboxMessage.objects.get()
        with task_login(ServiceRole.MAIL_DISPATCH, exact=True):
            execution = claim(message)
            begin(message, execution)
            finish_submission(
                message.pk, execution.claim, FamilyDeliveryResult(Status.UNKNOWN, 1)
            )
        act(_status(TaskRun.objects.get(pk=message.task_id)), "permanent_failure")
        producer = DailyDigestFinalizeProducer(uuid4())
        with (
            task_login(ServiceRole.SCHEDULER, exact=True),
            scheduler_session() as guard,
        ):
            assert producer(guard) == ()
        message.refresh_from_db()
        # Owner-level synthetic external evidence isolates finalization from the
        # separately tested interactive Admin authorization/attestation workflow.
        change(
            message_status(message), DeliveryAction.ACCEPT, evidence=provider_evidence()
        )
        occurrence = ScheduleOccurrence.objects.get(
            pk=ready.snapshot.preparation.occurrence_id
        )
        assert occurrence.state == "pending"
        with (
            task_login(ServiceRole.SCHEDULER, exact=True),
            scheduler_session() as guard,
        ):
            (task,) = producer(guard)
            assert producer(guard) == ()
        with task_login(ServiceRole.WORKER, exact=True):
            arguments = dict(
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={TASK_TYPE: finalization_handler()},
            )
            if crash_after_claim:
                assert claim_hint(task.run_id, **arguments) is not None
            else:
                assert execute_hint(task.run_id, **arguments)
        if crash_after_claim:
            running = act(
                _status(TaskRun.objects.get(pk=task.run_id)),
                "heartbeat",
                lease_seconds=1,
            )
            expire(running)
            with task_login(ServiceRole.WORKER, exact=True):
                assert recover_hint(task.run_id, **arguments)
        occurrence.refresh_from_db()
        assert occurrence.state == "succeeded"
        assert (
            ScheduleFulfillment.objects.filter(
                occurrence=occurrence, disposition="delivered"
            ).count()
            == 1
        )
        assert TaskRun.objects.get(pk=message.task_id).state == "failed"
        assert TaskRun.objects.get(pk=task.run_id).state == "succeeded"
        with (
            task_login(ServiceRole.SCHEDULER, exact=True),
            scheduler_session() as guard,
        ):
            assert producer(guard) == ()
