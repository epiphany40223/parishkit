"""Activation preparation retains complete groups across bounded worker effects."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from django.db import IntegrityError, OperationalError, transaction

from parishkit.stewardship.campaigns.boundaries import apply_due_boundaries
from parishkit.stewardship.campaigns.catchup_preparation import prepare_batch
from parishkit.stewardship.campaigns.catchup_tasks import (
    TASK_TYPE,
    admit_catchup,
    catchup_handler,
)
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    CatchUpCheckpoint,
    CatchUpFailure,
    ScheduleFulfillment,
    ScheduleOccurrence,
    ScheduleRecoveryReplacement,
)
from parishkit.stewardship.campaigns.recovery_coverage import covered_dates
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint, execute_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.outbox_models import OutboxMessage
from parishkit.stewardship.jobs.ownership import TaskOwnershipLost
from parishkit.stewardship.jobs.storage import _status, retry_failed

from ..campaign_factory import campaign as campaign_record
from .campaign_builders import (
    admit_test_work,
    campaign_clock,
    change,
    claimed_task,
    command,
    draft_campaign,
    restored_runtime,
)
from .credential_builders import family_campaign, populate
from .test_background_grants_postgresql import task_login
from .test_digest_schedule_planning_postgresql import add_digest
from .test_family_schedule_planning_postgresql import add_reminders
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


def execution_arguments(demand):
    """Use the compiled registry and real dispatcher; never a callback permit."""
    return dict(
        run_id=demand.task_root_id,
        queue=catchup_handler().queue,
        worker_id=uuid4(),
        handlers={TASK_TYPE: catchup_handler()},
    )


def test_family_cutoff_group_is_prepared_and_not_dispatched(tmp_path):
    """Delayed worker startup cannot absorb schedules due after activation."""
    store, campaign, actor, _ = family_campaign(tmp_path)
    add_reminders(store, campaign, actor)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
    demand = ActivationCatchUpDemand.objects.get()
    with (
        campaign_clock(datetime(2026, 10, 25, tzinfo=UTC)),
        task_login(ServiceRole.WORKER, exact=True),
    ):
        assert execute_hint(**execution_arguments(demand))
    demand.refresh_from_db()
    assert demand.completed_at is not None
    assert ScheduleOccurrence.objects.count() == 3
    assert ScheduleOccurrence.objects.filter(state="coalesced").count() == 2
    selected = ScheduleOccurrence.objects.get(state="pending")
    assert selected.definition.kind == "initial"
    assert set(ScheduleFulfillment.objects.values_list("occurrence_id", flat=True)) == {
        selected.pk
    }
    assert TaskRun.objects.get(pk=demand.task_root_id).state == "succeeded"
    assert not OutboxMessage.objects.exists()


def test_transient_database_failure_records_sanitized_retry_and_retains_hold(
    tmp_path, monkeypatch
):
    """Only the still-live worker can record failure; no exception text is retained."""
    _, campaign, actor, _ = family_campaign(tmp_path)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()

        def fail(*args, **kwargs):
            """Inject a safely rolled-back local failure, not provider uncertainty."""
            raise OperationalError("synthetic-private-database-detail")

        monkeypatch.setattr(
            "parishkit.stewardship.campaigns.catchup_preparation.prepare_batch", fail
        )
        with task_login(ServiceRole.WORKER, exact=True):
            assert execute_hint(**execution_arguments(demand))
    demand.refresh_from_db()
    assert demand.completed_at is None and demand.failure_code == "outcome_failed"
    assert CatchUpFailure.objects.get().code == "outcome_failed"
    assert TaskRun.objects.get(pk=demand.task_root_id).state == "retry_wait"
    assert not CatchUpCheckpoint.objects.exists()


def test_digest_preparation_spans_pages_without_releasing_partial_coverage(tmp_path):
    """More than 100 dates require separate materialization and coverage commits."""
    store, campaign, actor = draft_campaign(
        tmp_path,
        campaign_record(start_date="2026-01-01", end_date="2026-12-31"),
    )
    definition = add_digest(store, campaign)
    with campaign_clock(datetime(2026, 4, 20, 12, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(**execution_arguments(demand))
            with maintain_execution(execution):
                for _ in range(3):
                    with execution.effect():
                        prepare_batch(demand, execution.claim)
                    demand.refresh_from_db()
                    assert demand.completed_at is None
                assert (
                    ScheduleOccurrence.objects.filter(definition_id=definition).count()
                    == 109
                )
                assert not ScheduleFulfillment.objects.exists()
                with execution.effect():
                    prepare_batch(demand, execution.claim)
                demand.refresh_from_db()
                assert demand.completed_at is None
                assert ScheduleFulfillment.objects.count() == 99
                execution.handler.execute(execution)
    demand.refresh_from_db()
    assert demand.completed_at is not None
    assert ScheduleFulfillment.objects.count() == 109
    selected = ScheduleOccurrence.objects.get(state="pending")
    assert selected.slot == f"recovery:{demand.pk}"
    assert set(ScheduleFulfillment.objects.values_list("occurrence_id", flat=True)) == {
        selected.pk
    }
    assert max(CatchUpCheckpoint.objects.values_list("items", flat=True)) <= 100
    assert not OutboxMessage.objects.exists()


def test_failed_family_effect_cannot_commit_its_checkpoint(tmp_path, monkeypatch):
    """A failed group leaves both its outcomes and traversal cursor uncommitted."""
    _, campaign, actor, _ = family_campaign(tmp_path)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        execution = claim_hint(**execution_arguments(demand))
        with maintain_execution(execution):
            # Failure inside the actual planner rolls back the group and cursor.
            def fail(*args, **kwargs):
                """Inject failure before the owning Family result is committed."""
                raise RuntimeError("synthetic preparation failure")

            monkeypatch.setattr(
                "parishkit.stewardship.campaigns.catchup_preparation.plan_family", fail
            )
            with pytest.raises(RuntimeError, match="synthetic preparation"):
                execution.handler.execute(execution)
    demand.refresh_from_db()
    assert demand.cursor == "" and demand.completed_at is None
    assert not CatchUpCheckpoint.objects.exists()


def test_restricted_worker_cannot_forge_empty_completion(tmp_path):
    """A live lease does not replace the cohort and digest receipt requirements."""
    _, campaign, actor, _ = family_campaign(tmp_path)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(**execution_arguments(demand))
            campaign.refresh_from_db()
            prefix = campaign.active_configuration_id.hex + ":"
            with (
                transaction.atomic(),
                pytest.raises(IntegrityError, match="cohort and digest coverage"),
            ):
                CatchUpCheckpoint.objects.create(
                    demand=demand,
                    sequence=1,
                    group_key=prefix + "complete",
                    cursor=prefix + "complete:",
                    items=0,
                    phase="complete",
                    complete=True,
                    task_id=execution.claim.run_id,
                    fence=execution.claim.fence,
                    actor_id=execution.claim.worker_id,
                    correlation_id=execution.claim.run_id,
                )
    demand.refresh_from_db()
    assert demand.completed_at is None and not CatchUpCheckpoint.objects.exists()


def test_failure_after_family_outcomes_rolls_back_them_and_cursor(
    tmp_path, monkeypatch
):
    """There is no committed gap between materialized outcomes and their receipt."""
    _, campaign, actor, _ = family_campaign(tmp_path)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        execution = claim_hint(**execution_arguments(demand))

        def fail(*args, **kwargs):
            """Fail after the real planner, at the transactional receipt boundary."""
            assert ScheduleOccurrence.objects.exists()
            raise RuntimeError("synthetic checkpoint failure")

        with maintain_execution(execution), monkeypatch.context() as patch:
            patch.setattr(
                "parishkit.stewardship.campaigns.catchup_preparation._checkpoint", fail
            )
            with pytest.raises(RuntimeError, match="synthetic checkpoint"):
                execution.handler.execute(execution)
    demand.refresh_from_db()
    assert demand.cursor == "" and not ScheduleOccurrence.objects.exists()


def test_stale_fence_cannot_prepare_any_occurrence(tmp_path):
    """Typed stale claims fail before staging, regardless of a valid domain UUID."""
    from dataclasses import replace

    _, campaign, actor, _ = family_campaign(tmp_path)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        execution = claim_hint(**execution_arguments(demand))
        with maintain_execution(execution), execution.effect():
            stale = replace(execution.claim, fence=execution.claim.fence + 1)
            with pytest.raises(TaskOwnershipLost):
                prepare_batch(demand, stale)
    assert not ScheduleOccurrence.objects.exists()


def test_campaign_close_skips_remaining_family_work_without_clearing_other_holds(
    tmp_path,
):
    """Closing first still leaves actual preparation responsible for its cutoff."""
    _, campaign, actor, _ = family_campaign(tmp_path)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
    demand = ActivationCatchUpDemand.objects.get()
    run = claimed_task("campaign_boundary", campaign.pk, actor)
    with campaign_clock(campaign.active_configuration.ends_at):
        apply_due_boundaries(
            campaign_id=campaign.pk,
            task_id=run.run_id,
            fence=run.fence,
            actor_id=actor,
            correlation_id=uuid4(),
            admit=admit_test_work,
        )
        with task_login(ServiceRole.WORKER, exact=True):
            assert execute_hint(**execution_arguments(demand))
    demand.refresh_from_db()
    assert demand.completed_at is not None
    row = ScheduleOccurrence.objects.get()
    assert row.state == "skipped" and row.reason == "campaign_closed"
    assert not ScheduleFulfillment.objects.exists()


def test_restore_blocks_claim_and_post_claim_effect_without_losing_demand(tmp_path):
    """Restored work remains durable; a prior hint cannot bypass maintenance."""
    _, campaign, actor, _ = family_campaign(tmp_path)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        with restored_runtime(demand.cutoff), pytest.raises(PermissionError):
            claim_hint(**execution_arguments(demand))
        execution = claim_hint(**execution_arguments(demand))
        with (
            maintain_execution(execution),
            restored_runtime(demand.cutoff),
            pytest.raises(PermissionError),
        ):
            execution.handler.execute(execution)
    demand.refresh_from_db()
    assert demand.completed_at is None and demand.cursor == ""


def test_explicit_failed_task_retry_resumes_original_demand_and_receipts(tmp_path):
    """A new canonical execution leaves original failure and first Family intact."""
    _, campaign, actor, _ = family_campaign(tmp_path, count=2)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        execution = claim_hint(**execution_arguments(demand))
        with maintain_execution(execution), execution.effect():
            prepare_batch(demand, execution.claim)
        first = CatchUpCheckpoint.objects.get()
        # Controlled terminal-failure injection uses the normal task state
        # machine; actual retry admission and its new execution are compiled.
        failed = act(
            _status(TaskRun.objects.get(pk=demand.task_root_id)), "permanent_failure"
        )
        command_id = uuid4()
        with work_transaction():
            options = dict(
                run_id=failed.run_id,
                command_id=command_id,
                actor_id=actor,
                correlation_id=uuid4(),
                admit=admit_catchup,
            )
            retried = retry_failed(**options)
            assert retry_failed(**options).run_id == retried.run_id
        with task_login(ServiceRole.WORKER, exact=True):
            assert execute_hint(
                **(execution_arguments(demand) | {"run_id": retried.run_id})
            )
    demand.refresh_from_db()
    assert demand.completed_at is not None and demand.task_root_id == failed.root_id
    assert TaskRun.objects.get(pk=failed.run_id).state == "failed"
    assert TaskRun.objects.get(pk=retried.run_id).state == "succeeded"
    assert CatchUpCheckpoint.objects.filter(pk=first.pk).exists()
    assert ScheduleOccurrence.objects.count() == 2


def test_live_source_recheck_skips_inactive_family_and_keeps_new_targets_out_of_cohort(
    tmp_path,
):
    """Pinned traversal is finite while eligibility comes from current source truth."""
    _, campaign, actor, rings = family_campaign(tmp_path, count=2)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        execution = claim_hint(**execution_arguments(demand))
        families = list(FamilyCampaign.objects.order_by("id"))
        with maintain_execution(execution):
            with execution.effect():
                prepare_batch(demand, execution.claim)
            populate(
                campaign,
                rings,
                [
                    FamilyStatus(families[0].family_duid, True, True, True, True),
                    FamilyStatus(families[1].family_duid, False, False, False, False),
                    FamilyStatus(3, True, True, True, True),
                ],
                generation=2,
            )
            with task_login(ServiceRole.WORKER, exact=True):
                execution.handler.execute(execution)
    assert (
        FamilyCampaign.objects.count() == 3 and ScheduleOccurrence.objects.count() == 2
    )
    skipped = ScheduleOccurrence.objects.get(state="skipped")
    assert skipped.target == f"family:{families[1].pk}"
    assert skipped.reason == "family_ineligible"


def test_revision_change_after_partial_coverage_retains_all_original_dates(tmp_path):
    """Cancelled aggregate lineage preserves dates across another current revision."""
    store, campaign, actor = draft_campaign(
        tmp_path,
        campaign_record(start_date="2026-01-01", end_date="2026-12-31"),
    )
    definition = add_digest(store, campaign)
    with campaign_clock(datetime(2026, 4, 20, 12, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        execution = claim_hint(**execution_arguments(demand))
        with maintain_execution(execution):
            for _ in range(4):
                with execution.effect():
                    prepare_batch(demand, execution.claim)
            previous = ScheduleOccurrence.objects.get(slot=f"recovery:{demand.pk}")
            assert ScheduleFulfillment.objects.filter(occurrence=previous).count() == 99
            result = change(
                store,
                store.active(),
                actor,
                [
                    {
                        "operation": "update",
                        "section": "schedules",
                        "id": str(definition),
                        "values": {"time": "01:15:00"},
                    }
                ],
            )
            assert result.state == "applied"
            previous.refresh_from_db()
            assert (
                previous.state == "skipped" and previous.reason == "schedule_replaced"
            )
            with task_login(ServiceRole.WORKER, exact=True):
                execution.handler.execute(execution)
    selected = ScheduleOccurrence.objects.get(state="pending")
    assert (
        ScheduleRecoveryReplacement.objects.get(previous=previous).replacement_id
        == selected.pk
    )
    first = covered_dates(selected.pk)
    second = covered_dates(selected.pk, after=first[-1])
    assert len(first) == 100 and len(second) == 9
    assert len(set(first + second)) == 109
    assert ScheduleFulfillment.objects.filter(occurrence=previous).count() == 99
    assert not OutboxMessage.objects.exists()
