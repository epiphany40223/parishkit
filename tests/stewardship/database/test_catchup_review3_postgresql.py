"""Final-review regressions for excluded lineage and current Family evidence."""

from datetime import UTC, datetime

import pytest
from django.db import IntegrityError

from parishkit.stewardship.campaigns import catchup_digest
from parishkit.stewardship.campaigns.catchup_ownership import claim_event
from parishkit.stewardship.campaigns.catchup_preparation import prepare_batch
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    CatchUpCheckpoint,
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
    ScheduleRecoveryReplacement,
)
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.ownership import TaskOwnershipLost

from ..campaign_factory import schedule
from .campaign_builders import campaign_clock, change, command, draft_campaign
from .credential_builders import family_campaign
from .response_builders import activate_response_service
from .test_background_grants_postgresql import task_login
from .test_catchup_preparation_postgresql import execution_arguments
from .test_catchup_restore_proofs_postgresql import hold_slots, stage_family_pending
from .test_digest_schedule_planning_postgresql import add_digest
from .test_family_schedule_planning_postgresql import add_reminders
from .test_response_submission_postgresql import form_and_answers, submit

pytestmark = pytest.mark.django_db(transaction=True)


def test_worker_cannot_forward_family_coverage_to_a_restore_held_selection(
    tmp_path, monkeypatch
):
    """A held current initial is not a substitute for the selected current reminder."""
    store, campaign, actor, _ = family_campaign(tmp_path)
    family = FamilyCampaign.objects.get()
    initial = ScheduleDefinition.objects.get()
    add_reminders(store, campaign, actor)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        execution = claim_hint(**execution_arguments(demand))
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            prepare_batch(demand, execution.claim)
        previous = ScheduleOccurrence.objects.get(state="pending")
        extra = schedule(
            str(campaign.pk), kind="reminder", date="2026-10-04", time="12:00:00"
        )
        assert (
            change(
                store,
                store.active(),
                actor,
                [
                    {
                        "operation": "update",
                        "section": "schedules",
                        "id": str(initial.pk),
                        "values": {"time": "02:15:00"},
                    },
                    {"operation": "add", "section": "schedules", **extra},
                ],
            ).state
            == "applied"
        )
        with task_login(ServiceRole.WORKER, exact=True):
            stage_family_pending(
                demand, execution.claim, family, definition_ids=[initial.pk]
            )
        held = ScheduleOccurrence.objects.get(state="pending")
        (hold,) = hold_slots([held], campaign, actor)

        def wrong_successor(demand, claim, family_id, selected_id):
            """Choose the excluded row instead of the owner's actual candidate."""
            assert selected_id != held.pk
            ScheduleRecoveryReplacement.objects.create(
                demand=demand,
                previous=previous,
                replacement=held,
                actor_id=claim.worker_id,
                correlation_id=claim_event(claim),
            )

        with monkeypatch.context() as patch:
            patch.setattr(
                "parishkit.stewardship.campaigns.catchup_family_coverage.forward_family_coverage",
                wrong_successor,
            )
            with (
                task_login(ServiceRole.WORKER, exact=True),
                work_transaction(),
                pytest.raises(IntegrityError, match="Recovery replacement requires"),
            ):
                prepare_batch(demand, execution.claim)
        assert not ScheduleRecoveryReplacement.objects.exists()
        assert CatchUpCheckpoint.objects.count() == 1
        with task_login(ServiceRole.WORKER, exact=True), maintain_execution(execution):
            execution.handler.execute(execution)
    demand.refresh_from_db()
    hold.refresh_from_db()
    edge = ScheduleRecoveryReplacement.objects.get(previous=previous)
    assert str(edge.replacement.definition_id) == extra["id"]
    assert edge.replacement_id != held.pk and hold.state == "unreviewed"
    assert demand.completed_at is not None


@pytest.mark.parametrize("forgery", ["disposition", "replacement"])
def test_coalesced_family_coverage_rejects_inconsistent_semantic_evidence(
    tmp_path, monkeypatch, forgery
):
    """The writer guard also rejects the corrupt rows a receipt must never trust."""
    store, campaign, actor, _ = family_campaign(tmp_path)
    add_reminders(store, campaign, actor)
    original = ScheduleFulfillment.objects.create

    def corrupt(**values):
        """Change one semantic invariant before the actual guarded INSERT."""
        if forgery == "disposition":
            values["disposition"] = "delivered"
        else:
            values["occurrence_id"] = ScheduleOccurrence.objects.get(
                definition_id=values["definition_id"], state="coalesced"
            ).pk
        return original(**values)

    monkeypatch.setattr(ScheduleFulfillment.objects, "create", corrupt)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(**execution_arguments(demand))
            with (
                work_transaction(),
                pytest.raises(IntegrityError, match="exact semantic outcome"),
            ):
                prepare_batch(demand, execution.claim)
    assert not ScheduleOccurrence.objects.exists()
    assert not ScheduleFulfillment.objects.exists()
    assert not CatchUpCheckpoint.objects.exists()


def test_worker_receipt_cannot_leave_mail_pending_for_a_live_responder(
    response_service,
):
    """An actual accepted submission must invalidate a stale pending mail decision."""
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        harness = activate_response_service(response_service)
        family = FamilyCampaign.objects.get(campaign=harness.campaign, family_duid=1)
        demand = ActivationCatchUpDemand.objects.get()
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(**execution_arguments(demand))
            stage_family_pending(demand, execution.claim, family)
        form, answers = form_and_answers(harness)
        answers["testing_acknowledged"] = False
        assert submit(harness, form, answers).submission is not None
        prefix = harness.campaign.active_configuration_id.hex + ":"
        with (
            task_login(ServiceRole.WORKER, exact=True),
            work_transaction(),
            pytest.raises(IntegrityError, match="Family receipt lacks"),
        ):
            CatchUpCheckpoint.objects.create(
                demand=demand,
                sequence=1,
                group_key=prefix + f"family:{family.pk}",
                cursor=prefix + "families:" + family.pk.hex,
                items=1,
                phase="families",
                task_id=execution.claim.run_id,
                fence=execution.claim.fence,
                actor_id=execution.claim.worker_id,
                correlation_id=execution.claim.run_id,
            )
    assert not CatchUpCheckpoint.objects.exists()


def test_ownership_loss_before_aggregate_creation_commits_no_effect(
    tmp_path, monkeypatch
):
    """The immediate Python fence failure remains typed, not a later SQL error."""
    store, campaign, actor = draft_campaign(tmp_path)
    add_digest(store, campaign)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(**execution_arguments(demand))
            for _ in range(2):
                with work_transaction():
                    prepare_batch(demand, execution.claim)

            def lost(claim):
                """Inject lease loss precisely at the aggregate's per-effect check."""
                raise TaskOwnershipLost("Synthetic lost claim")

            monkeypatch.setattr(catchup_digest, "lock_task_claim", lost)
            with work_transaction(), pytest.raises(TaskOwnershipLost):
                prepare_batch(demand, execution.claim)
    assert CatchUpCheckpoint.objects.count() == 2
    assert not ScheduleOccurrence.objects.filter(slot__startswith="recovery:").exists()
    assert not ScheduleFulfillment.objects.exists()
