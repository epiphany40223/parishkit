"""Finite recurring slots survive restart without granting digest dispatch."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.campaigns.digest_schedule_planning import (
    DigestScheduleProducer,
)
from parishkit.stewardship.campaigns.models import (
    RestoreDeliveryHold,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.resolutions import resolve_restore_hold
from parishkit.stewardship.campaigns.schedules import record_fulfillment
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.scheduler import scheduler_session

from ..campaign_factory import schedule
from .campaign_builders import (
    admit_test_work,
    advance,
    campaign_clock,
    change,
    claimed_task,
    close_campaign,
    complete_empty_catchup,
    restored_runtime,
)
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_family_auth_postgresql import family_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def add_digest(store, campaign, *, weekly=False):
    """Create the versioned recurring definition using real installation."""
    row = schedule(
        str(campaign.pk),
        kind="weekly_digest" if weekly else "daily_digest",
        date=None,
        weekday=2 if weekly else None,
        time="00:15:00",
    )
    assert (
        change(
            store,
            store.active(),
            uuid4(),
            [{"operation": "add", "section": "schedules", **row}],
        ).state
        == "applied"
    )
    return UUID(row["id"])


def test_bounded_daily_pages_and_restart_keep_same_persisted_instants(
    family_service,  # noqa: F811
    auth_service,
):
    campaign = family_service.campaign
    identifier = add_digest(auth_service.store, campaign)
    producer = DigestScheduleProducer(uuid4(), limit=2)
    with (
        campaign_clock(datetime(2026, 10, 10, tzinfo=UTC)),
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session() as guard,
    ):
        (first,) = producer(guard)
        (second,) = producer(guard)
        assert first.definition_id == second.definition_id == identifier
        assert first.created == second.created == 2
        (replay,) = DigestScheduleProducer(uuid4(), limit=2)(guard)
        assert replay.created == 0 and replay.occurrences == first.occurrences
        assert len(ScheduleOccurrence.objects.filter(definition_id=identifier)) == 4
    rows = list(
        ScheduleOccurrence.objects.filter(definition_id=identifier).order_by("slot")
    )
    assert [row.slot for row in rows] == [
        "2026-10-01",
        "2026-10-02",
        "2026-10-03",
        "2026-10-04",
    ]
    assert all(
        row.target == "admins" and row.state == "pending" and row.task_id is None
        for row in rows
    )
    assert not OutboxMessage.objects.exists()


def test_daily_backlog_does_not_starve_weekly_definition(
    family_service,  # noqa: F811
    auth_service,
):
    campaign = family_service.campaign
    identifiers = {
        add_digest(auth_service.store, campaign),
        add_digest(auth_service.store, campaign, weekly=True),
    }
    producer = DigestScheduleProducer(uuid4(), limit=1)
    with (
        campaign_clock(datetime(2026, 10, 20, tzinfo=UTC)),
        scheduler_session() as guard,
    ):
        (first,) = producer(guard)
        (second,) = producer(guard)
        assert {first.definition_id, second.definition_id} == identifiers
        assert first.created == second.created == 1


def test_exhausted_cursor_resumes_only_new_due_dates(
    family_service,  # noqa: F811
    auth_service,
):
    """Normal polling after exhaustion does not repeatedly scan campaign history."""
    add_digest(auth_service.store, family_service.campaign)
    producer = DigestScheduleProducer(uuid4())
    with scheduler_session() as guard:
        with campaign_clock(datetime(2026, 10, 4, tzinfo=UTC)):
            (first,) = producer(guard)
            assert first.created == 2
            assert list(producer.cursors.values()) == [date(2026, 10, 2)]
            (repeat,) = producer(guard)
            assert repeat.created == 0 and repeat.occurrences == ()
        with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
            (later,) = producer(guard)
            assert later.created == 1
            assert ScheduleOccurrence.objects.get(pk=later.occurrences[0]).slot == (
                "2026-10-03"
            )


def test_digest_coverage_and_restore_holds_survive_restart_and_resolution(
    family_service,  # noqa: F811
    auth_service,
):
    """Retained coverage excludes mail; changed holds revisit past cursor dates."""
    campaign, actor = family_service.campaign, uuid4()
    identifier = add_digest(auth_service.store, campaign)
    with campaign_clock(datetime(2026, 10, 3, tzinfo=UTC)):
        with scheduler_session() as guard:
            (first,) = DigestScheduleProducer(actor)(guard)
        row = ScheduleOccurrence.objects.get(pk=first.occurrences[0])
        run = claimed_task("schedule_occurrence", row.pk, actor)
        row = advance(row, actor, "running", task_id=run.run_id, fence=run.fence)
        advance(row, actor, "succeeded", fence=run.fence)
        record_fulfillment(
            occurrence_id=row.pk,
            disposition="delivered",
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
    start = campaign.active_configuration.starts_at
    with restored_runtime(start) as restore_id:
        hold = RestoreDeliveryHold.objects.create(
            restore_id=restore_id,
            definition_id=identifier,
            mode="testing",
            target="admins",
            slot="2026-10-02",
            backup_at=start,
            window_start=start,
            window_end=start + timedelta(days=3),
            discovery="inventory",
            actor_id=actor,
            correlation_id=uuid4(),
        )
    producer = DigestScheduleProducer(actor)
    with (
        campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)),
        scheduler_session() as guard,
    ):
        (result,) = producer(guard)
        assert result.created == 1 and len(result.occurrences) == 1
        assert ScheduleOccurrence.objects.get(pk=result.occurrences[0]).slot == (
            "2026-10-03"
        )
        assert not ScheduleOccurrence.objects.filter(slot="2026-10-02").exists()
        (replay,) = DigestScheduleProducer(actor)(guard)
        assert replay.created == 0 and replay.occurrences == result.occurrences
        resolve_restore_hold(
            hold_id=hold.pk,
            expected_version=hold.version,
            state="not_applicable",
            evidence="Synthetic inventory correction",
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        (released,) = producer(guard)
        assert released.created == 1
        assert ScheduleOccurrence.objects.filter(slot="2026-10-02").count() == 1
        assert row.pk not in released.occurrences


def test_revised_digest_restarts_evaluation_without_rewriting_old_slots(
    family_service,  # noqa: F811
    auth_service,
):
    campaign = family_service.campaign
    identifier = add_digest(auth_service.store, campaign)
    producer = DigestScheduleProducer(uuid4(), limit=1)
    with (
        campaign_clock(datetime(2026, 10, 10, tzinfo=UTC)),
        scheduler_session() as guard,
    ):
        (first,) = producer(guard)
        original = ScheduleOccurrence.objects.get(pk=first.occurrences[0])
        assert (
            change(
                auth_service.store,
                auth_service.store.active(),
                uuid4(),
                [
                    {
                        "operation": "update",
                        "section": "schedules",
                        "id": str(identifier),
                        "values": {"time": "00:30:00"},
                    }
                ],
            ).state
            == "applied"
        )
        (next_page,) = producer(guard)
        replacement = ScheduleOccurrence.objects.get(pk=next_page.occurrences[0])
        original.refresh_from_db()
        assert original.state == "skipped" and original.reason == "schedule_replaced"
        assert replacement.slot == original.slot
        assert replacement.revision_id != original.revision_id
        assert replacement.due_at > original.due_at


def test_no_definition_and_fake_owner_have_no_effect(family_service):  # noqa: F811
    producer = DigestScheduleProducer(uuid4())
    with pytest.raises(TypeError, match="scheduler ownership"):
        producer(object())
    with scheduler_session() as guard:
        assert producer(guard) == ()


def test_activation_catchup_holds_ordinary_materialization(
    response_service, auth_service
):
    campaign = response_service.campaign
    add_digest(auth_service.store, campaign)
    activate_response_service(response_service)
    producer = DigestScheduleProducer(uuid4())
    with (
        campaign_clock(datetime(2026, 10, 10, tzinfo=UTC)),
        scheduler_session() as guard,
    ):
        assert producer(guard) == ()
        assert not ScheduleOccurrence.objects.exists()
        complete_empty_catchup(campaign, uuid4())
        (result,) = producer(guard)
        assert result.created > 0


def test_final_daily_slot_after_close_is_finite_and_never_dispatched(
    response_service, auth_service
):
    campaign = response_service.campaign
    identifier = add_digest(auth_service.store, campaign)
    activate_response_service(response_service)
    close_campaign(campaign, uuid4())
    producer = DigestScheduleProducer(uuid4())
    with (
        campaign_clock(campaign.active_configuration.ends_at + timedelta(days=30)),
        task_login(ServiceRole.SCHEDULER, exact=True),
        scheduler_session() as guard,
    ):
        (result,) = producer(guard)
        assert result.created == 31
        (again,) = producer(guard)
        assert again.created == 0
    final = (
        ScheduleOccurrence.objects.filter(definition_id=identifier)
        .order_by("slot")
        .last()
    )
    assert (
        final.slot == "2026-10-31"
        and final.due_at > campaign.active_configuration.ends_at
    )
    assert not OutboxMessage.objects.exists()


def test_weekly_materialization_does_not_invent_endless_postclose_obligations(
    response_service, auth_service
):
    campaign = response_service.campaign
    identifier = add_digest(auth_service.store, campaign, weekly=True)
    activate_response_service(response_service)
    close_campaign(campaign, uuid4())
    producer = DigestScheduleProducer(uuid4())
    with (
        campaign_clock(campaign.active_configuration.ends_at + timedelta(days=365)),
        scheduler_session() as guard,
    ):
        (first,) = producer(guard)
        assert first.created == 4
        (second,) = producer(guard)
        assert second.created == 0
    assert ScheduleOccurrence.objects.filter(definition_id=identifier).count() == 4
