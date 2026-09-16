"""Complete Family groups persist once without provider calls or delivery hints."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction
from django.db.models import F

from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.campaigns.family_schedule_planning import plan_family
from parishkit.stewardship.campaigns.models import (
    RestoreDeliveryHold,
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.schedule_production import FamilyScheduleProducer
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.scheduler import (
    SchedulerOwnershipLost,
    scheduler_session,
)

from ..campaign_factory import schedule
from .campaign_builders import (
    advance,
    campaign_clock,
    change,
    claimed_task,
    close_campaign,
    complete_empty_catchup,
    restored_runtime,
)
from .credential_builders import populate
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_auth_postgresql import family_service  # noqa: F401
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)


def add_reminders(store, campaign, actor):
    """Add three reminders through actual configuration selection and revisions."""
    rows = [
        schedule(str(campaign.pk), kind="reminder", date=f"2026-10-{day:02}")
        for day in (3, 4, 20)
    ]
    assert (
        change(
            store,
            store.active(),
            actor,
            [{"operation": "add", "section": "schedules", **row} for row in rows],
        ).state
        == "applied"
    )
    return rows


def test_complete_family_group_coalesces_once_and_preserves_future_schedule(
    family_service,  # noqa: F811
    auth_service,
):
    campaign, actor = family_service.campaign, uuid4()
    family_id = FamilyCampaign.objects.get().pk
    reminders = add_reminders(auth_service.store, campaign, actor)
    cutoff = ScheduleDefinition.objects.get(
        pk=UUID(reminders[1]["id"])
    ).current_revision.due_at
    with campaign_clock(cutoff), scheduler_session() as guard:
        first = plan_family(guard, family_id=family_id, worker_id=actor)
        assert (first.created, first.coalesced, first.skipped, first.held) == (
            3,
            2,
            0,
            False,
        )
        selected = ScheduleOccurrence.objects.get(pk=first.selected)
        assert selected.definition.kind == "initial" and selected.state == "pending"
        second = plan_family(guard, family_id=family_id, worker_id=actor)
        assert second.created == second.coalesced == second.skipped == 0
        assert second.selected == first.selected
        assert ScheduleOccurrence.objects.count() == 3
        assert set(
            ScheduleFulfillment.objects.values_list("disposition", flat=True)
        ) == {"coalesced"}
        assert set(
            ScheduleFulfillment.objects.values_list("occurrence_id", flat=True)
        ) == {first.selected}
        assert not OutboxMessage.objects.exists()


def test_future_and_prestart_planning_does_not_allocate(family_service):  # noqa: F811
    campaign = family_service.campaign
    family_id = FamilyCampaign.objects.get().pk
    with (
        campaign_clock(campaign.active_configuration.starts_at),
        scheduler_session() as guard,
    ):
        result = plan_family(guard, family_id=family_id, worker_id=uuid4())
        assert result.created == 0 and result.selected is None
    assert not ScheduleOccurrence.objects.exists()


def test_unknown_family_is_denied_and_fake_guard_cannot_produce(family_service):  # noqa: F811
    family_id = FamilyCampaign.objects.get().pk
    with pytest.raises(TypeError, match="scheduler ownership"):
        plan_family(object(), family_id=family_id, worker_id=uuid4())
    with scheduler_session() as guard, pytest.raises(PermissionError, match="outside"):
        plan_family(guard, family_id=uuid4(), worker_id=uuid4())


def test_bound_running_work_holds_recovery_group(family_service, auth_service):  # noqa: F811
    actor = uuid4()
    family_id = FamilyCampaign.objects.get().pk
    initial = ScheduleDefinition.objects.get()
    with campaign_clock(initial.current_revision.due_at), scheduler_session() as guard:
        first = plan_family(guard, family_id=family_id, worker_id=actor)
        row = ScheduleOccurrence.objects.get(pk=first.selected)
        task = claimed_task("schedule_occurrence", row.pk, actor)
        advance(row, actor, "running", task_id=task.run_id, fence=task.fence)
        held = plan_family(guard, family_id=family_id, worker_id=actor)
        assert held.held and held.reason == "delivery_unresolved"
        row.refresh_from_db()
        assert row.state == "running"


def test_terminal_failure_is_not_retried_by_scheduler(family_service):  # noqa: F811
    family_id, actor = FamilyCampaign.objects.get().pk, uuid4()
    definition = ScheduleDefinition.objects.get()
    with (
        campaign_clock(definition.current_revision.due_at),
        scheduler_session() as guard,
    ):
        first = plan_family(guard, family_id=family_id, worker_id=actor)
        row = ScheduleOccurrence.objects.get(pk=first.selected)
        task = claimed_task("schedule_occurrence", row.pk, actor)
        advance(row, actor, "running", task_id=task.run_id, fence=task.fence)
        advance(row, actor, "failed", fence=task.fence, reason="definitive_failure")
        result = plan_family(guard, family_id=family_id, worker_id=actor)
        assert result.created == 0 and result.selected is None
    row.refresh_from_db()
    assert row.state == "failed" and ScheduleOccurrence.objects.count() == 1


def test_exact_scheduler_role_can_plan_without_private_columns(
    family_service,  # noqa: F811
    auth_service,
):
    campaign, actor = family_service.campaign, uuid4()
    family_id = FamilyCampaign.objects.get().pk
    rows = add_reminders(auth_service.store, campaign, actor)
    cutoff = ScheduleDefinition.objects.get(
        pk=UUID(rows[1]["id"])
    ).current_revision.due_at
    with (
        campaign_clock(cutoff),
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session() as guard,
    ):
        result = plan_family(guard, family_id=family_id, worker_id=actor)
        assert result.created == 3 and result.coalesced == 2


def test_producer_paging_restart_and_new_family_are_idempotent(family_service):  # noqa: F811
    campaign, actor = family_service.campaign, uuid4()
    populate(
        campaign,
        family_service.rings,
        [FamilyStatus(n, True, True, True, True) for n in range(1, 4)],
        generation=2,
    )
    initial = ScheduleDefinition.objects.get()
    producer = FamilyScheduleProducer(actor, limit=1)
    with (
        campaign_clock(initial.current_revision.due_at),
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session() as guard,
    ):
        first = producer(guard)
        second = producer(guard)
        third = producer(guard)
        assert len(first) == len(second) == len(third) == 1
        assert len({row.family_id for row in (*first, *second, *third)}) == 3
        assert producer(guard) == () and producer.cursor is None
        replay = FamilyScheduleProducer(actor)(guard)
        assert len(replay) == 3 and all(row.created == 0 for row in replay)
    assert ScheduleOccurrence.objects.count() == 3


def test_scheduler_cannot_claim_or_rewrite_owned_work(family_service):  # noqa: F811
    family_id, actor = FamilyCampaign.objects.get().pk, uuid4()
    definition = ScheduleDefinition.objects.get()
    with (
        campaign_clock(definition.current_revision.due_at),
        scheduler_session() as guard,
    ):
        first = plan_family(guard, family_id=family_id, worker_id=actor)
        row = ScheduleOccurrence.objects.get(pk=first.selected)
        task = claimed_task("schedule_occurrence", row.pk, actor)
        advance(row, actor, "running", task_id=task.run_id, fence=task.fence)
        with task_login(ServiceRole.SCHEDULER, exact=True):
            with (
                pytest.raises(IntegrityError, match="unallocated pending"),
                transaction.atomic(),
            ):
                ScheduleOccurrence.objects.filter(pk=row.pk).update(
                    state="skipped", reason="family_responded", version=F("version") + 1
                )
            for statement in (
                "SELECT code_ciphertext FROM stewardship_family_campaign",
                "SELECT annual_pledge FROM stewardship_submission",
                "SELECT * FROM stewardship_outbox_message",
                "UPDATE stewardship_schedule_occurrence SET worker_id=NULL",
            ):
                with (
                    pytest.raises(ProgrammingError, match="permission denied") as error,
                    transaction.atomic(),
                    connection.cursor() as cursor,
                ):
                    cursor.execute(statement)
                assert error.value.__cause__.sqlstate == "42501"
    row.refresh_from_db()
    assert row.state == "running"


def test_no_recipient_records_skip_but_no_message(family_service):  # noqa: F811
    campaign, actor = family_service.campaign, uuid4()
    family_id = FamilyCampaign.objects.get().pk
    populate(
        campaign,
        family_service.rings,
        [FamilyStatus(1, True, True, True, False)],
        generation=2,
    )
    definition = ScheduleDefinition.objects.get()
    with (
        campaign_clock(definition.current_revision.due_at),
        scheduler_session() as guard,
    ):
        result = plan_family(guard, family_id=family_id, worker_id=actor)
        assert result.created == result.skipped == 1 and result.selected is None
        assert plan_family(guard, family_id=family_id, worker_id=actor).created == 0
    assert ScheduleOccurrence.objects.get().reason == "no_deliverable_recipient"
    assert (
        not OutboxMessage.objects.exists() and not ScheduleFulfillment.objects.exists()
    )


def test_corrected_recipient_retains_diagnostic_initial_hold(
    family_service,  # noqa: F811
    auth_service,
):
    """BG-06 owns a new catch-up initial; ordinary planning cannot bypass it."""
    campaign, actor = family_service.campaign, uuid4()
    family_id = FamilyCampaign.objects.get().pk
    populate(
        campaign,
        family_service.rings,
        [FamilyStatus(1, True, True, True, False)],
        generation=2,
    )
    definition = ScheduleDefinition.objects.get()
    with (
        campaign_clock(definition.current_revision.due_at),
        scheduler_session() as guard,
    ):
        assert plan_family(guard, family_id=family_id, worker_id=actor).skipped == 1
    initial = ScheduleOccurrence.objects.get()
    populate(
        campaign,
        family_service.rings,
        [FamilyStatus(1, True, True, True, True)],
        generation=3,
    )
    reminders = add_reminders(auth_service.store, campaign, actor)
    due = ScheduleDefinition.objects.get(
        pk=UUID(reminders[1]["id"])
    ).current_revision.due_at
    with campaign_clock(due), scheduler_session() as guard:
        result = plan_family(guard, family_id=family_id, worker_id=actor)
        assert result.held and result.reason == "initial_unfulfilled"
        assert result.selected is None and result.coalesced == result.skipped == 0
    initial.refresh_from_db()
    assert initial.state == "skipped" and initial.reason == "no_deliverable_recipient"
    assert not OutboxMessage.objects.exists()


def test_source_inactivation_skips_existing_work_without_new_allocation(family_service):  # noqa: F811
    campaign, actor = family_service.campaign, uuid4()
    family_id = FamilyCampaign.objects.get().pk
    definition = ScheduleDefinition.objects.get()
    with (
        campaign_clock(definition.current_revision.due_at),
        scheduler_session() as guard,
    ):
        plan_family(guard, family_id=family_id, worker_id=actor)
        populate(
            campaign,
            family_service.rings,
            [FamilyStatus(1, False, False, False, False)],
            generation=2,
        )
        result = plan_family(guard, family_id=family_id, worker_id=actor)
        assert result.created == 0 and result.skipped == 1
    assert ScheduleOccurrence.objects.get().reason == "family_ineligible"


def test_missed_family_slot_after_close_gets_durable_skip(response_service):
    campaign, actor = response_service.campaign, uuid4()
    activate_response_service(response_service)
    close_campaign(campaign, actor)
    family_id = FamilyCampaign.objects.get(family_duid=1).pk
    with (
        campaign_clock(campaign.active_configuration.ends_at),
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session() as guard,
    ):
        result = plan_family(guard, family_id=family_id, worker_id=actor)
        assert result.created == result.skipped == 1 and result.selected is None
    row = ScheduleOccurrence.objects.get()
    assert row.state == "skipped" and row.reason == "campaign_closed"
    assert (
        not OutboxMessage.objects.exists() and not ScheduleFulfillment.objects.exists()
    )


@pytest.mark.parametrize("live", [False, True])
def test_real_submission_suppresses_pending_family_mail(response_service, live):
    harness = activate_response_service(response_service) if live else response_service
    campaign, actor = harness.campaign, uuid4()
    if live:
        complete_empty_catchup(campaign, actor)
    family_id = FamilyCampaign.objects.get(family_duid=1).pk
    definition = ScheduleDefinition.objects.get()
    with (
        campaign_clock(definition.current_revision.due_at),
        scheduler_session() as guard,
    ):
        first = plan_family(guard, family_id=family_id, worker_id=actor)
        assert first.created == 1
        form, answers = form_and_answers(harness)
        answers["testing_acknowledged"] = not live
        assert submit(harness, form, answers).submission is not None
        result = plan_family(guard, family_id=family_id, worker_id=actor)
        assert result.created == 0 and result.skipped == 1 and result.selected is None
    assert ScheduleOccurrence.objects.get().reason == "family_responded"


def test_restore_held_initial_is_not_consumed_by_reminder_recovery(
    family_service,  # noqa: F811
    auth_service,
):
    campaign, actor = family_service.campaign, uuid4()
    family_id = FamilyCampaign.objects.get().pk
    initial = ScheduleDefinition.objects.get()
    start = campaign.active_configuration.starts_at
    with restored_runtime(start) as restore_id:
        RestoreDeliveryHold.objects.create(
            restore_id=restore_id,
            definition=initial,
            mode="testing",
            target=f"family:{family_id}",
            slot="once",
            backup_at=start,
            window_start=start,
            window_end=start + timedelta(days=1),
            discovery="inventory",
            actor_id=actor,
            correlation_id=uuid4(),
        )
        with scheduler_session() as guard:
            assert plan_family(guard, family_id=family_id, worker_id=actor).held
    rows = add_reminders(auth_service.store, campaign, actor)
    cutoff = ScheduleDefinition.objects.get(
        pk=UUID(rows[1]["id"])
    ).current_revision.due_at
    with campaign_clock(cutoff), scheduler_session() as guard:
        result = plan_family(guard, family_id=family_id, worker_id=actor)
        assert result.created == 2 and result.coalesced == 1
    assert not ScheduleOccurrence.objects.filter(definition=initial).exists()
    assert not ScheduleFulfillment.objects.filter(definition=initial).exists()
    assert ScheduleOccurrence.objects.get(pk=result.selected).definition_id == UUID(
        rows[1]["id"]
    )


def test_interrupted_complete_group_rolls_back_and_restarts(
    family_service,  # noqa: F811
    auth_service,
    monkeypatch,
):
    """Ownership loss cannot leave a partial Family choice or coverage behind."""
    campaign, actor = family_service.campaign, uuid4()
    family_id = FamilyCampaign.objects.get().pk
    rows = add_reminders(auth_service.store, campaign, actor)
    cutoff = ScheduleDefinition.objects.get(
        pk=UUID(rows[1]["id"])
    ).current_revision.due_at
    with campaign_clock(cutoff), scheduler_session() as guard:
        original_check, calls = guard.check, 0

        def lose_ownership():
            """Interrupt after allocation has begun, before group commit."""
            nonlocal calls
            calls += 1
            original_check()
            if calls == 4:
                raise SchedulerOwnershipLost("Injected interrupted planning")

        with monkeypatch.context() as patch:
            patch.setattr(guard, "check", lose_ownership)
            with pytest.raises(SchedulerOwnershipLost, match="Injected"):
                plan_family(guard, family_id=family_id, worker_id=actor)
        assert not ScheduleOccurrence.objects.exists()
        assert not ScheduleFulfillment.objects.exists()
        result = plan_family(guard, family_id=family_id, worker_id=actor)
        assert result.created == 3 and result.coalesced == 2
