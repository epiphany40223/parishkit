"""Successful weekly intervals advance only after an empty or resolved cohort."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.stewardship.campaigns.schedule_models import (
    OccurrenceTransition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.delivery_states import DeliveryAction
from parishkit.stewardship.jobs.dispatch import claim_hint, execute_hint, recover_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_storage import _status as message_status
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports.digest_finalization import (
    WEEKLY_TASK_TYPE,
    WeeklyDigestFinalizeProducer,
    finalization_handler,
)
from parishkit.stewardship.reports.weekly_capture import retained_history
from parishkit.stewardship.reports.weekly_models import (
    WeeklyDigestPreparation,
    WeeklyDigestRecipient,
)

from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_outbox_postgresql import change, provider_evidence, submit
from .test_response_revisit_postgresql import revisit
from .test_response_submission_postgresql import submit as respond_form
from .test_taskrun_postgresql import act, expire
from .test_weekly_coverage_postgresql import accepted, publish, replacement
from .test_weekly_fanout_postgresql import captured, messages
from .test_weekly_observation_postgresql import respond

pytestmark = pytest.mark.django_db(transaction=True)
INSTANT = datetime(2026, 10, 8, tzinfo=UTC)


def history(snapshot):
    """Read the real SQL-derived history under current general-worker authority."""
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        return retained_history(
            WeeklyDigestPreparation.objects.get(pk=snapshot.preparation_id)
        )


def test_only_the_last_accepted_admin_advances_the_successful_boundary(
    live_response_service,
):
    respond(live_response_service, "Two-recipient obligation")
    with campaign_clock(INSTANT):
        claim, snapshot = captured(
            live_response_service, additional_admins=("second@example.org",)
        )
        publish(claim)
        rows = list(
            WeeklyDigestRecipient.objects.filter(snapshot=snapshot).order_by("address")
        )
        accepted(rows[0])
        assert history(snapshot).watermark == 0
        assert history(snapshot).reported == frozenset(
            UUID(item) for item in snapshot.information
        )
        occurrence = ScheduleOccurrence.objects.get(
            pk=snapshot.preparation.occurrence_id
        )
        assert occurrence.state == "pending"
        accepted(rows[1])
        occurrence.refresh_from_db()
        assert (
            occurrence.state == "succeeded"
            and occurrence.reason == "weekly_digest_complete"
        )
        assert history(snapshot).watermark == snapshot.submission_watermark
        assert (
            ScheduleFulfillment.objects.get(
                occurrence=occurrence, slot=occurrence.slot
            ).disposition
            == "delivered"
        )
        assert list(
            OccurrenceTransition.objects.filter(occurrence=occurrence)
            .order_by("version")
            .values_list("after_state", flat=True)
        )[-2:] == ["running", "succeeded"]


def test_empty_success_advances_sequence_without_losing_later_corrections(
    live_response_service,
):
    harness = live_response_service
    respond(harness, "Retain this original request")
    with campaign_clock(INSTANT):
        claim, first = captured(harness)
        publish(claim)
        accepted(WeeklyDigestRecipient.objects.get(snapshot=first))
        assert history(first).watermark == 1
    with campaign_clock(datetime(2026, 10, 15, tzinfo=UTC)):
        harness, form, answers, _ = revisit(harness)
        respond_form(harness, form, answers)
        claim, empty = replacement()
        assert empty.information == empty.corrections == []
        publish(claim)
        occurrence = ScheduleOccurrence.objects.get(pk=empty.preparation.occurrence_id)
        assert (
            occurrence.state == "succeeded"
            and occurrence.reason == "weekly_digest_empty"
        )
        assert (
            ScheduleFulfillment.objects.get(
                occurrence=occurrence, slot=occurrence.slot
            ).disposition
            == "empty"
        )
        assert history(empty).watermark == 2
        assert messages().count() == 1
    with campaign_clock(datetime(2026, 10, 22, tzinfo=UTC)):
        harness, form, answers, _ = revisit(harness)
        answers["additional_information"] = ""
        respond_form(harness, form, answers)
        claim, correction = replacement()
        assert correction.information == []
        assert correction.corrections == [[first.information[0], "withdrawn"]]
        publish(claim)
        recipient = WeeklyDigestRecipient.objects.get(snapshot=correction)
        assert "Retain this original request" not in recipient.text
        accepted(recipient)
        assert history(correction).watermark == 3
        assert len(history(correction).corrected) == 1


@pytest.mark.parametrize("crash", [False, True])
def test_late_acceptance_uses_fresh_metadata_finalizer(live_response_service, crash):
    respond(live_response_service, "Late external acceptance")
    with campaign_clock(INSTANT):
        claim, snapshot = captured(live_response_service)
        publish(claim)
        submitted = submit(message_status(messages().get()))
        uncertain = change(
            submitted, DeliveryAction.MARK_UNKNOWN, evidence=provider_evidence()
        )
        act(_status(TaskRun.objects.get(pk=submitted.task_id)), "permanent_failure")
        change(uncertain, DeliveryAction.ACCEPT, evidence=provider_evidence())
        occurrence = ScheduleOccurrence.objects.get(
            pk=snapshot.preparation.occurrence_id
        )
        assert occurrence.state == "pending" and history(snapshot).watermark == 0
        producer = WeeklyDigestFinalizeProducer(uuid4())
        with (
            task_login(ServiceRole.SCHEDULER, exact=True),
            scheduler_session() as guard,
        ):
            (task,) = producer(guard)
            assert producer(guard) == ()
        arguments = dict(
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={WEEKLY_TASK_TYPE: finalization_handler()},
        )
        with task_login(ServiceRole.WORKER, exact=True):
            if crash:
                assert claim_hint(task.run_id, **arguments) is not None
            else:
                assert execute_hint(task.run_id, **arguments)
        if crash:
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
        assert history(snapshot).watermark == 1
        assert TaskRun.objects.get(pk=submitted.task_id).state == "failed"
        assert TaskRun.objects.get(pk=task.run_id).state == "succeeded"
        assert (
            ScheduleFulfillment.objects.filter(
                occurrence=occurrence, disposition="delivered"
            ).count()
            == 1
        )


def test_raw_fulfillment_cannot_claim_delivery_for_empty_interval(response_service):
    with campaign_clock(INSTANT):
        claim, snapshot = captured(response_service)
        publish(claim)
        occurrence = ScheduleOccurrence.objects.get(
            pk=snapshot.preparation.occurrence_id
        )
        with (
            pytest.raises(DatabaseError, match="semantic outcome"),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "INSERT INTO stewardship_schedule_fulfillment "
                "(id,definition_id,mode,target,slot,disposition,"
                "occurrence_id,correlation_id) "
                "VALUES(%s,%s,%s,%s,%s,'delivered',%s,%s)",
                [
                    uuid4(),
                    occurrence.definition_id,
                    occurrence.mode,
                    occurrence.target,
                    occurrence.slot,
                    occurrence.pk,
                    uuid4(),
                ],
            )
