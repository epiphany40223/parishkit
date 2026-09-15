"""Finite recurring slots survive restart without granting digest dispatch."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from parishkit.stewardship.campaigns.digest_schedule_planning import (
    DigestScheduleProducer,
)
from parishkit.stewardship.campaigns.models import ScheduleOccurrence
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.scheduler import scheduler_session

from ..campaign_factory import schedule
from .campaign_builders import (
    campaign_clock,
    change,
    close_campaign,
    complete_empty_catchup,
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
