"""Durable source transitions recover initial mail without rewriting history."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction

from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    FamilyEligibilityChange,
)
from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.campaigns.family_schedule_planning import plan_family
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.campaigns.schedules import occurrence_key
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.scheduler import scheduler_session

from .campaign_builders import advance, campaign_clock, claimed_task, command
from .credential_builders import family_campaign, populate
from .test_background_grants_postgresql import task_login
from .test_catchup_preparation_postgresql import execution_arguments
from .test_family_auth_postgresql import family_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def test_source_recovery_can_finish_held_activation_preparation(tmp_path):
    """A source correction cannot strand the bounded activation worker forever."""
    _, campaign, actor, rings = family_campaign(tmp_path)
    family = FamilyCampaign.objects.get()
    definition = ScheduleDefinition.objects.get()
    populate(campaign, rings, [FamilyStatus(1, True, True, True, False)], generation=2)
    with campaign_clock(definition.current_revision.due_at):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(**execution_arguments(demand))
        with maintain_execution(execution):
            with task_login(ServiceRole.WORKER, exact=True), execution.effect():
                skipped = plan_family(
                    execution.claim,
                    family_id=family.pk,
                    worker_id=execution.claim.worker_id,
                )
                assert skipped.created == skipped.skipped == 1
            populate(
                campaign, rings, [FamilyStatus(1, True, True, True, True)], generation=3
            )
            with task_login(ServiceRole.WORKER, exact=True):
                execution.handler.execute(execution)
        demand.refresh_from_db()
        assert demand.completed_at is not None
        recovered = ScheduleOccurrence.objects.get(state="pending")
        assert recovered.recovery_generation > 0
        assert ScheduleOccurrence.objects.filter(state="skipped").count() == 1


def status(harness, *, deliverable, generation):
    """Write a genuine population transition, including its immutable SQL history."""
    populate(
        harness.campaign,
        harness.rings,
        [FamilyStatus(1, True, True, True, deliverable)],
        generation=generation,
    )


def plan(guard, family_id, actor):
    """Run allocation as the real, metadata-only scheduler role."""
    with task_login(ServiceRole.SCHEDULER, exact=True):
        return plan_family(guard, family_id=family_id, worker_id=actor)


def test_repeated_transitions_allocate_once_per_generation(family_service):  # noqa: F811
    """A terminal recovery may get a later attempt, never a new fulfillment slot."""
    actor, family = uuid4(), FamilyCampaign.objects.get()
    definition = ScheduleDefinition.objects.get()
    status(family_service, deliverable=False, generation=2)
    with (
        campaign_clock(definition.current_revision.due_at),
        scheduler_session() as guard,
    ):
        first = plan(guard, family.pk, actor)
        assert first.created == first.skipped == 1
        status(family_service, deliverable=True, generation=3)
        recovery = plan(guard, family.pk, actor)
        assert recovery.created == 1 and recovery.selected is not None
        first_recovery = ScheduleOccurrence.objects.get(pk=recovery.selected)
        status(family_service, deliverable=True, generation=4)
        assert plan(guard, family.pk, actor).created == 0
        status(family_service, deliverable=False, generation=5)
        assert plan(guard, family.pk, actor).skipped == 1
        status(family_service, deliverable=True, generation=6)
        second = plan(guard, family.pk, actor)
        second_recovery = ScheduleOccurrence.objects.get(pk=second.selected)
        assert second.created == 1
        assert second_recovery.recovery_generation > first_recovery.recovery_generation
        assert first_recovery.slot == second_recovery.slot == "once"
        assert plan(guard, family.pk, actor).selected == second.selected
    assert ScheduleOccurrence.objects.count() == 3
    assert ScheduleOccurrence.objects.filter(state="skipped").count() == 2


@pytest.mark.parametrize("outcome", ["running", "delivery_unknown", "failed"])
def test_transition_during_owned_attempt_never_authorizes_resend(
    family_service,  # noqa: F811
    outcome,
):
    """An edge must follow failure; unresolved provider ownership is never failure."""
    actor, family = uuid4(), FamilyCampaign.objects.get()
    definition = ScheduleDefinition.objects.get()
    with (
        campaign_clock(definition.current_revision.due_at),
        scheduler_session() as guard,
    ):
        selected = plan(guard, family.pk, actor).selected
        row = ScheduleOccurrence.objects.get(pk=selected)
        task = claimed_task("schedule_occurrence", row.pk, actor)
        advance(row, actor, "running", task_id=task.run_id, fence=task.fence)
        status(family_service, deliverable=False, generation=2)
        status(family_service, deliverable=True, generation=3)
        if outcome != "running":
            advance(row, actor, outcome, fence=task.fence, reason="provider_result")
        result = plan(guard, family.pk, actor)
        assert result.created == 0 and result.selected is None
        assert ScheduleOccurrence.objects.count() == 1
        assert result.held == (outcome != "failed")


@pytest.mark.parametrize("mutation", ["invented", "wrong_key", "wrong_target"])
def test_database_rejects_forged_recovery_evidence(family_service, mutation):  # noqa: F811
    """Direct scheduler inserts cannot invent a transition or move it to a Family."""
    actor, family = uuid4(), FamilyCampaign.objects.get()
    definition = ScheduleDefinition.objects.get()
    status(family_service, deliverable=False, generation=2)
    with (
        campaign_clock(definition.current_revision.due_at),
        scheduler_session() as guard,
    ):
        plan(guard, family.pk, actor)
        old = ScheduleOccurrence.objects.get()
        status(family_service, deliverable=True, generation=3)
        generation = (
            FamilyEligibilityChange.objects.filter(
                family=family, email_deliverable=True
            )
            .latest("family_version")
            .family_version
        )
        target = old.target
        if mutation == "invented":
            generation += 100
        elif mutation == "wrong_target":
            target = f"family:{uuid4()}"
        key = occurrence_key(
            old.revision_id, old.mode, target, old.slot, recovery_generation=generation
        )
        if mutation == "wrong_key":
            key = "f" * 64
        with (
            task_login(ServiceRole.SCHEDULER, exact=True),
            pytest.raises(IntegrityError, match="new deliverability transition"),
            transaction.atomic(),
        ):
            ScheduleOccurrence.objects.create(
                definition_id=old.definition_id,
                revision_id=old.revision_id,
                mode=old.mode,
                routing=old.routing,
                target=target,
                slot=old.slot,
                due_at=old.due_at,
                occurrence_key=key,
                recovery_generation=generation,
                actor_id=actor,
                correlation_id=uuid4(),
            )
        assert ScheduleOccurrence.objects.count() == 1
        assert plan(guard, family.pk, actor).created == 1
