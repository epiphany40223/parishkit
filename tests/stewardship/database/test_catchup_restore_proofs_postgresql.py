"""Preparation releases its own hold without consuming independent restore holds."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from parishkit.stewardship.campaigns import catchup_digest
from parishkit.stewardship.campaigns.catchup_ownership import claim_event
from parishkit.stewardship.campaigns.catchup_preparation import prepare_batch
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    RestoreDeliveryHold,
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.schedules import occurrence_key
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution

from .campaign_builders import campaign_clock, command, draft_campaign, restored_runtime
from .credential_builders import family_campaign
from .test_background_grants_postgresql import task_login
from .test_catchup_preparation_postgresql import execution_arguments
from .test_digest_schedule_planning_postgresql import add_digest
from .test_family_schedule_planning_postgresql import add_reminders

pytestmark = pytest.mark.django_db(transaction=True)


def stage_family_pending(demand, claim, family, *, definition_ids=None):
    """Model a crash after valid materialization but before group reconciliation."""
    with work_transaction():
        correlation = claim_event(claim)
        definitions = ScheduleDefinition.objects.select_related(
            "current_revision"
        ).filter(current_revision__due_at__lte=demand.cutoff)
        if definition_ids is not None:
            definitions = definitions.filter(pk__in=definition_ids)
        for definition in definitions:
            ScheduleOccurrence.objects.create(
                definition=definition,
                revision_id=definition.current_revision_id,
                mode="production",
                routing="production",
                target=f"family:{family.pk}",
                slot="once",
                due_at=definition.current_revision.due_at,
                occurrence_key=occurrence_key(
                    definition.current_revision_id,
                    "production",
                    f"family:{family.pk}",
                    "once",
                ),
                actor_id=claim.worker_id,
                correlation_id=correlation,
            )


def hold_slots(rows, campaign, actor):
    """Load restored inventory, then release only the global fixture gate."""
    start = campaign.active_configuration.starts_at
    with restored_runtime(start) as restore_id:
        return [
            RestoreDeliveryHold.objects.create(
                restore_id=restore_id,
                definition_id=row.definition_id,
                mode="production",
                target=row.target,
                slot=row.slot,
                backup_at=start,
                window_start=start,
                window_end=start + timedelta(days=10),
                discovery="inventory",
                actor_id=actor,
                correlation_id=uuid4(),
            )
            for row in rows
        ]


@pytest.mark.parametrize("held_count", [1, 2])
def test_family_restore_slots_do_not_block_other_group_preparation(
    tmp_path, held_count
):
    """Held initial/reminders remain pending but are not eligible selection inputs."""
    store, campaign, actor, _ = family_campaign(tmp_path)
    add_reminders(store, campaign, actor)
    family = FamilyCampaign.objects.get()
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(**execution_arguments(demand))
            stage_family_pending(demand, execution.claim, family)
        held = list(ScheduleOccurrence.objects.order_by("due_at")[:held_count])
        holds = hold_slots(held, campaign, actor)
        with task_login(ServiceRole.WORKER, exact=True), maintain_execution(execution):
            execution.handler.execute(execution)
    demand.refresh_from_db()
    assert demand.completed_at is not None
    assert (
        ScheduleOccurrence.objects.filter(
            pk__in=[row.pk for row in held], state="pending"
        ).count()
        == held_count
    )
    selected = ScheduleOccurrence.objects.exclude(pk__in=[row.pk for row in held]).get(
        state="pending"
    )
    assert selected.definition.kind == "reminder"
    assert selected.definition.current_revision.values["date"] == "2026-10-04"
    assert ScheduleFulfillment.objects.count() == 2 - held_count
    assert (
        RestoreDeliveryHold.objects.filter(
            pk__in=[row.pk for row in holds], state="unreviewed"
        ).count()
        == held_count
    )


def test_digest_aggregate_date_excludes_newest_restore_held_slot(tmp_path, monkeypatch):
    """SQL and Python date selection use identical original-slot exclusion sets."""
    store, campaign, actor = draft_campaign(tmp_path)
    definition = add_digest(store, campaign)
    resolved = []

    def resolve(claim):
        """Observe real event resolution without replacing its ownership check."""
        resolved.append(claim.run_id)
        return claim_event(claim)

    monkeypatch.setattr(catchup_digest, "claim_event", resolve)
    with campaign_clock(datetime(2026, 10, 5, 12, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(**execution_arguments(demand))
            for _ in range(2):
                with work_transaction():
                    prepare_batch(demand, execution.claim)
        newest = ScheduleOccurrence.objects.filter(definition_id=definition).latest(
            "due_at"
        )
        (hold,) = hold_slots([newest], campaign, actor)
        with task_login(ServiceRole.WORKER, exact=True), maintain_execution(execution):
            execution.handler.execute(execution)
    demand.refresh_from_db()
    assert demand.completed_at is not None
    selected = ScheduleOccurrence.objects.get(slot__startswith="recovery:")
    assert selected.due_at < newest.due_at
    assert ScheduleFulfillment.objects.count() == 3
    newest.refresh_from_db()
    hold.refresh_from_db()
    assert newest.state == "pending" and hold.state == "unreviewed"
    assert len(resolved) == 2  # One lookup per date/cover batch, not per effect.
