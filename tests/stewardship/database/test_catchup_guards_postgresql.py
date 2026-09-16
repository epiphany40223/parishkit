"""Actual worker credentials cannot manufacture preparation or erase live scope."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from queue import Queue
from threading import Barrier
from uuid import uuid4

import pytest
from django.db import IntegrityError, close_old_connections, connection, transaction

from parishkit.stewardship.campaigns.catchup_ownership import claim_event
from parishkit.stewardship.campaigns.catchup_preparation import prepare_batch
from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
from parishkit.stewardship.campaigns.family_schedule_planning import plan_family
from parishkit.stewardship.campaigns.lifecycle import Action
from parishkit.stewardship.campaigns.models import (
    ActivationCatchUpDemand,
    CatchUpCheckpoint,
    ScheduleDefinition,
    ScheduleFulfillment,
    ScheduleOccurrence,
    ScheduleRecoveryReplacement,
)
from parishkit.stewardship.campaigns.schedules import occurrence_key
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import campaign_clock, change, command, draft_campaign
from .credential_builders import family_campaign
from .lock_observer import backend_pid, wait_for_lock
from .test_background_grants_postgresql import task_login
from .test_catchup_preparation_postgresql import execution_arguments
from .test_digest_schedule_planning_postgresql import add_digest
from .test_family_schedule_planning_postgresql import add_reminders
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("forgery", ["digest", "page", "cover", "complete"])
def test_worker_cannot_forge_digest_inventory_receipts(tmp_path, forgery):
    """Knowing every predictable key and the genuine claim cannot prove coverage."""
    store, campaign, actor = draft_campaign(tmp_path)
    definition = add_digest(store, campaign)
    with campaign_clock(datetime(2026, 10, 20, 12, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        campaign.refresh_from_db()
        prefix = campaign.active_configuration_id.hex + ":"
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(**execution_arguments(demand))
            with work_transaction():
                prepare_batch(demand, execution.claim)  # Empty Family cohort only.
            key, cursor = {
                "digest": (f"digest:{definition}", "digests:"),
                "page": (
                    f"page:{definition}:2026-10-19:cover",
                    f"digests:{definition.hex}:cover:2026-10-19",
                ),
                "cover": (f"cover:{definition}:2", f"digests:{definition.hex}:cover:"),
                "complete": ("complete", "complete:"),
            }[forgery]
            with transaction.atomic(), pytest.raises(IntegrityError, match="Catch-up"):
                CatchUpCheckpoint.objects.create(
                    demand=demand,
                    sequence=2,
                    group_key=prefix + key,
                    cursor=prefix + cursor,
                    items=0,
                    phase="complete" if forgery == "complete" else "digests",
                    complete=forgery == "complete",
                    task_id=execution.claim.run_id,
                    fence=execution.claim.fence,
                    actor_id=execution.claim.worker_id,
                    correlation_id=execution.claim.run_id,
                )
    demand.refresh_from_db()
    assert demand.completed_at is None and not ScheduleOccurrence.objects.exists()
    assert CatchUpCheckpoint.objects.count() == 1


@pytest.mark.parametrize(
    "forgery", ["admins", "unknown_family", "slot", "due", "unfenced", "key"]
)
def test_worker_occurrence_writes_require_exact_family_slot_and_claim(
    tmp_path, forgery
):
    """Task membership alone cannot authorize arbitrary targets or due instants."""
    _, campaign, actor, _ = family_campaign(tmp_path)
    definition = ScheduleDefinition.objects.select_related("current_revision").get()
    family = FamilyCampaign.objects.get()
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(**execution_arguments(demand))
            with work_transaction():
                values = dict(
                    definition=definition,
                    revision_id=definition.current_revision_id,
                    mode="production",
                    routing="production",
                    target=f"family:{family.pk}",
                    slot="once",
                    due_at=definition.current_revision.due_at,
                    actor_id=execution.claim.worker_id,
                    correlation_id=claim_event(execution.claim),
                )
                values.update(
                    {
                        "admins": {"target": "admins"},
                        "unknown_family": {"target": f"family:{uuid4()}"},
                        "slot": {"slot": "invented"},
                        "due": {"due_at": values["due_at"] + timedelta(seconds=1)},
                        "unfenced": {"correlation_id": execution.claim.run_id},
                        "key": {},
                    }[forgery]
                )
                values["occurrence_key"] = (
                    "0" * 64
                    if forgery == "key"
                    else occurrence_key(
                        values["revision_id"],
                        "production",
                        values["target"],
                        values["slot"],
                    )
                )
                with transaction.atomic(), pytest.raises(IntegrityError):
                    ScheduleOccurrence.objects.create(**values)
    assert not ScheduleOccurrence.objects.exists()


def test_worker_cannot_skip_an_eligible_family_without_a_real_reason(tmp_path):
    """An authentic preparation claim cannot invent an ineligibility outcome."""
    _, campaign, actor, _ = family_campaign(tmp_path)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(**execution_arguments(demand))
            with work_transaction():
                prepare_batch(demand, execution.claim)
                row = ScheduleOccurrence.objects.get()
                with transaction.atomic(), pytest.raises(IntegrityError):
                    ScheduleOccurrence.objects.filter(pk=row.pk).update(
                        state="skipped",
                        reason="family_ineligible",
                        version=row.version + 1,
                    )
    assert ScheduleOccurrence.objects.get().state == "pending"


def test_prior_claim_event_cannot_authorize_writes_after_same_worker_reclaims(tmp_path):
    """Same run and worker UUID are insufficient when the lease fence advances."""
    _, campaign, actor, _ = family_campaign(tmp_path)
    definition = ScheduleDefinition.objects.select_related("current_revision").get()
    family = FamilyCampaign.objects.get()
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        options = execution_arguments(demand)
        execution = claim_hint(**options)
        with work_transaction():
            stale_event = claim_event(execution.claim)
            act(
                _status(TaskRun.objects.get(pk=demand.task_root_id)),
                "retryable_failure",
            )
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(1.05)")
        with task_login(ServiceRole.WORKER, exact=True):
            replacement = claim_hint(**options)
            assert replacement.claim.fence > execution.claim.fence
            with work_transaction():
                current_event = claim_event(replacement.claim)
                assert current_event != stale_event
                values = dict(
                    definition=definition,
                    revision_id=definition.current_revision_id,
                    mode="production",
                    routing="production",
                    target=f"family:{family.pk}",
                    slot="once",
                    due_at=definition.current_revision.due_at,
                    actor_id=execution.claim.worker_id,
                    correlation_id=stale_event,
                    occurrence_key=occurrence_key(
                        definition.current_revision_id,
                        "production",
                        f"family:{family.pk}",
                        "once",
                    ),
                )
                with transaction.atomic(), pytest.raises(IntegrityError):
                    ScheduleOccurrence.objects.create(**values)
                values["correlation_id"] = current_event
                ScheduleOccurrence.objects.create(**values)
    assert ScheduleOccurrence.objects.count() == 1


def test_digest_cannot_complete_coalescing_without_semantic_fulfillment(
    tmp_path, monkeypatch
):
    """A real planner bug omitting fulfillment rolls back the entire coverage batch."""
    store, campaign, actor = draft_campaign(tmp_path)
    definition = add_digest(store, campaign)
    with campaign_clock(datetime(2026, 10, 3, 12, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        with task_login(ServiceRole.WORKER, exact=True):
            execution = claim_hint(**execution_arguments(demand))
            for _ in range(2):
                with work_transaction():
                    prepare_batch(demand, execution.claim)
            assert (
                ScheduleOccurrence.objects.filter(definition_id=definition).count() == 2
            )
            monkeypatch.setattr(
                ScheduleFulfillment.objects, "create", lambda **kwargs: None
            )
            with (
                work_transaction(),
                pytest.raises(IntegrityError, match="inventory proof"),
            ):
                prepare_batch(demand, execution.claim)
    demand.refresh_from_db()
    assert demand.completed_at is None and demand.groups_completed == 2
    assert ScheduleOccurrence.objects.filter(state="pending").count() == 2
    assert not ScheduleFulfillment.objects.exists()


def test_catchup_family_planner_requires_its_open_ordered_transaction(tmp_path):
    """The pre-existing require_work_order check rejects autocommit before writes."""
    _, campaign, actor, _ = family_campaign(tmp_path)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        execution = claim_hint(
            **execution_arguments(ActivationCatchUpDemand.objects.get())
        )
        with pytest.raises(StorageInvariantError, match="ordered transaction"):
            plan_family(
                execution.claim,
                family_id=FamilyCampaign.objects.get().pk,
                worker_id=execution.claim.worker_id,
            )
    assert not ScheduleOccurrence.objects.exists()


@pytest.mark.parametrize("replacement", [False, True])
def test_two_connections_prepare_distinct_groups_for_the_same_claim(
    tmp_path, replacement
):
    """Ordered serialization reloads the committed cursor, not either caller's copy."""
    store, campaign, actor, _ = family_campaign(tmp_path, count=2)
    initial = ScheduleDefinition.objects.get()
    if replacement:
        add_reminders(store, campaign, actor)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        demand = ActivationCatchUpDemand.objects.get()
        execution = claim_hint(**execution_arguments(demand))
        if replacement:
            with work_transaction():
                prepare_batch(demand, execution.claim)
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
                        }
                    ],
                ).state
                == "applied"
            )
        with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
            barrier = Barrier(2)
            pids = Queue()
            # Only the observer returns to the fixture owner for pg_stat_activity.
            # New contender connections still inherit the restricted role hook.
            with connection.cursor() as cursor:
                cursor.execute("RESET SESSION AUTHORIZATION")

            def prepare(_):
                """Each contender uses an independent real restricted SQL connection."""
                close_old_connections()
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT current_user")
                        assert cursor.fetchone() == ("pk_stewardship_worker",)
                    pids.put(backend_pid())
                    barrier.wait(timeout=10)
                    with work_transaction():
                        receipt = prepare_batch(demand, execution.claim)
                        return receipt.group_key
                finally:
                    connection.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                with work_transaction():
                    futures = [pool.submit(prepare, index) for index in range(2)]
                    for _ in range(2):
                        wait_for_lock(pids.get(timeout=10))
                results = [future.result(timeout=10) for future in futures]
    assert len(set(results)) == 2
    assert CatchUpCheckpoint.objects.count() == (3 if replacement else 2)
    assert ScheduleOccurrence.objects.filter(state="pending").count() == 2
    assert ScheduleRecoveryReplacement.objects.count() == int(replacement)


def test_web_cannot_close_campaign_without_guarded_transition_evidence(tmp_path):
    """Definer token effects do not bypass the independent campaign write guard."""
    _, campaign, actor, _ = family_campaign(tmp_path)
    with campaign_clock(datetime(2026, 10, 5, tzinfo=UTC)):
        command(campaign, actor, Action.ACTIVATE)
        with (
            task_login(ServiceRole.WEB, exact=True),
            connection.cursor() as cursor,
            pytest.raises(IntegrityError),
        ):
            cursor.execute(
                "UPDATE stewardship_campaign SET state='closed',version=version+1,"
                "actor_id=%s,correlation_id=%s WHERE id=%s",
                [actor, uuid4(), campaign.pk],
            )
    campaign.refresh_from_db()
    assert campaign.state == "active"
