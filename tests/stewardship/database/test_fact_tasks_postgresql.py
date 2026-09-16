"""Compiled producer/worker tests using actual SQL roles and owned execution."""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.reports.fact_production import produce_facts
from parishkit.stewardship.reports.fact_tasks import TASK_TYPE, fact_handler
from parishkit.stewardship.reports.materialization import verify_fact_set
from parishkit.stewardship.reports.models import (
    CampaignDailyFactSet,
    CampaignFactPointer,
    CampaignFactRebuildDemand,
    FactBuildReceipt,
)

from .test_background_grants_postgresql import task_login
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


def produce():
    """Make initial hints already due, without sleeping or changing task lease time."""
    with transaction.atomic():
        instant = database_now() - timedelta(seconds=6)
    with (
        patch(
            "parishkit.stewardship.reports.demand.database_now", return_value=instant
        ),
        scheduler_session() as guard,
    ):
        return produce_facts(guard)


def execute(run_id):
    """Use actual dispatch, fences, heartbeat lifetime and compiled calculation."""
    return execute_hint(
        run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: fact_handler()},
    )


def test_real_producer_and_worker_publish_both_scopes(response_service):
    roots = produce()
    assert len(roots) == 2
    assert produce() == ()
    for root in roots:
        assert execute(root)
        assert TaskRun.objects.get(pk=root).state == "succeeded"
        receipt = FactBuildReceipt.objects.get(task_id=root)
        assert verify_fact_set(receipt.fact_set_id, admit=permit) == ()
        assert not execute(root)
    assert CampaignFactPointer.objects.count() == 2
    assert set(CampaignFactPointer.objects.values_list("version", flat=True)) == {1}
    assert not CampaignFactRebuildDemand.objects.filter(
        claimed_generation__isnull=False
    ).exists()
    assert produce() == ()


def test_actual_scheduler_and_worker_grants(response_service):
    with task_login(ServiceRole.SCHEDULER, exact=True):
        roots = produce()
        with pytest.raises(PermissionError, match="scheduler"):
            fact_handler(scheduler=True).execute(None)
    assert len(roots) == 2
    with task_login(ServiceRole.WORKER, exact=True):
        for root in roots:
            assert execute(root)
    assert FactBuildReceipt.objects.count() == 2


def test_failed_calculation_does_not_publish_partial_generation(
    response_service, monkeypatch
):
    from parishkit.stewardship.reports import materialization

    root = produce()[0]

    def fail(context):
        """Simulate process failure after durable input freezing, before staging."""
        raise RuntimeError("synthetic interruption")

    monkeypatch.setattr(materialization, "calculate_participation", fail)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        execute(root)
    assert TaskRun.objects.get(pk=root).state == "running"
    assert CampaignDailyFactSet.objects.get().state == "building"
    assert not CampaignFactPointer.objects.exists()
    assert not FactBuildReceipt.objects.exists()


def test_reused_exact_ready_generation_has_independent_completion_proof(
    response_service,
):
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.reports.demand import request_rebuild
    from parishkit.stewardship.reports.facts import fact_inputs
    from parishkit.stewardship.reports.materialization import materialize_fact_set

    from .test_fact_materialization_postgresql import allocation

    record, claim = allocation(response_service)
    materialize_fact_set(record.pk, claim, admit=permit)
    assert not CampaignFactPointer.objects.exists()
    # Match the ordinary producer's today-bounded tuple, without advancing it to
    # tomorrow merely because the synthetic helper otherwise builds two days.
    inputs = fact_inputs(record)
    with (
        work_transaction(),
        patch(
            "parishkit.stewardship.reports.demand.database_now",
            return_value=database_now() - timedelta(seconds=6),
        ),
    ):
        request_rebuild(inputs, admit=permit)
    with patch(
        "parishkit.stewardship.reports.fact_production._now",
        return_value=response_service.campaign.active_configuration.starts_at
        + timedelta(days=1),
    ):
        roots = produce()
    historical = next(
        root
        for root in roots
        if CampaignFactRebuildDemand.objects.get(
            pk=TaskRun.objects.get(pk=root).domain_request_id
        ).population_scope
        == "historical"
    )
    with task_login(ServiceRole.WORKER, exact=True):
        assert execute(historical)
    assert FactBuildReceipt.objects.get(task_id=historical).fact_set_id == record.pk
    assert (
        CampaignFactPointer.objects.get(population_scope="historical").fact_set_id
        == record.pk
    )


def test_completion_receipt_cannot_be_forged_before_claim(response_service):
    roots = produce()
    run = TaskRun.objects.get(pk=roots[0])
    with pytest.raises(IntegrityError), transaction.atomic():
        FactBuildReceipt.objects.create(
            task=run,
            run=run,
            demand_id=run.domain_request_id,
            revision=1,
            task_fence=1,
            worker_id=uuid4(),
            fact_set_id=uuid4(),
        )
    assert not FactBuildReceipt.objects.exists()


def test_retry_preserves_frozen_inputs_and_newer_pending_window(
    response_service, monkeypatch
):
    """Actual retry-root transfer finishes its old date before scheduling tomorrow."""
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.storage import _status, retry_failed
    from parishkit.stewardship.reports import materialization

    from .test_taskrun_postgresql import act

    root = produce()[0]
    original = materialization.calculate_participation

    def fail(context):
        """Leave a real owned generation behind, without changing task truth."""
        raise RuntimeError("synthetic crash")

    with monkeypatch.context() as changed:
        changed.setattr(materialization, "calculate_participation", fail)
        with pytest.raises(RuntimeError, match="synthetic crash"):
            execute(root)
    record = CampaignDailyFactSet.objects.get()
    old_date = record.through_date
    tomorrow = response_service.campaign.active_configuration.starts_at + timedelta(
        days=1
    )
    with patch(
        "parishkit.stewardship.reports.fact_production._now", return_value=tomorrow
    ):
        assert produce() == ()
    demand = CampaignFactRebuildDemand.objects.get(
        pk=TaskRun.objects.get(pk=root).domain_request_id
    )
    assert demand.pending_revision == 2 and demand.pending_due_at is not None
    with work_transaction():
        failed = act(_status(TaskRun.objects.get(pk=root)), "permanent_failure")
        retry = retry_failed(
            run_id=failed.run_id,
            command_id=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=fact_handler().admit,
        )
    with task_login(ServiceRole.WORKER, exact=True):
        assert execute(retry.run_id)
    record.refresh_from_db()
    assert record.state == "ready" and record.through_date == old_date
    assert record.task_id == retry.run_id
    demand.refresh_from_db()
    assert demand.claimed_generation_id is None
    assert demand.pending_revision == 2 and demand.pending_due_at is not None
    assert materialization.calculate_participation is original
    with patch(
        "parishkit.stewardship.reports.fact_production._now", return_value=tomorrow
    ):
        followups = produce()
    assert len(followups) == 1


def test_midnight_rollover_builds_new_day_without_new_responses(response_service):
    roots = produce()
    for root in roots:
        execute(root)
    tomorrow = response_service.campaign.active_configuration.starts_at + timedelta(
        days=1
    )
    with patch(
        "parishkit.stewardship.reports.fact_production._now", return_value=tomorrow
    ):
        followups = produce()
    assert len(followups) == 2
    for root in followups:
        execute(root)
    assert {
        row.fact_set.expected_count
        for row in CampaignFactPointer.objects.select_related("fact_set")
    } == {2}


def test_receipt_immutability_and_atomic_acknowledgment(response_service, monkeypatch):
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.dispatch import Execution
    from parishkit.stewardship.jobs.storage import _status, retry_failed
    from parishkit.stewardship.reports import fact_tasks

    from .test_taskrun_postgresql import act

    root = produce()[0]
    original = Execution.transition

    def crash(self, action, **options):
        """Roll back receipt/demand release if final task acknowledgment fails."""
        if action == "complete":
            raise RuntimeError("synthetic lost acknowledgment")
        return original(self, action, **options)

    with monkeypatch.context() as changed:
        changed.setattr(Execution, "transition", crash)
        with pytest.raises(RuntimeError, match="lost acknowledgment"):
            execute(root)
    assert not FactBuildReceipt.objects.exists()
    assert CampaignDailyFactSet.objects.get().state == "ready"
    assert (
        CampaignFactRebuildDemand.objects.get(
            claimed_task_id=root
        ).claimed_generation_id
        is not None
    )
    assert not CampaignFactPointer.objects.exists()

    def no_recalculation(*args, **kwargs):
        """A ready checkpoint must be reused, not recalculated after a crash."""
        pytest.fail("Ready generation was recalculated")

    monkeypatch.setattr(fact_tasks, "materialize_fact_set", no_recalculation)
    with work_transaction():
        failed = act(_status(TaskRun.objects.get(pk=root)), "permanent_failure")
        retry = retry_failed(
            run_id=failed.run_id,
            command_id=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=fact_handler().admit,
        )
    with task_login(ServiceRole.WORKER, exact=True):
        assert execute(retry.run_id)
    assert FactBuildReceipt.objects.filter(task_id=root).count() == 1
    assert CampaignFactPointer.objects.count() == 1
    assert not CampaignFactRebuildDemand.objects.filter(
        claimed_task_id=retry.run_id
    ).exists()


@pytest.mark.parametrize("freeze", [False, True])
def test_retry_exhaustion_preserves_explicit_retry_and_pending_window(
    response_service, freeze
):
    """Exhaustion is visible failure, not permission to discard or restart work."""
    from django.db import connection

    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.dispatch import recover_hint
    from parishkit.stewardship.jobs.ownership import TaskClaim
    from parishkit.stewardship.jobs.storage import _status, retry_failed
    from parishkit.stewardship.reports.demand import claim_rebuild

    from .test_taskrun_postgresql import act, expire

    root = produce()[1]
    status = _status(TaskRun.objects.get(pk=root))
    for _ in range(4):
        status = act(act(status, "claim"), "retryable_failure", retry_seconds=1)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(1.05)")
    status = act(status, "claim", lease_seconds=1)
    assert status.attempt == 5
    if freeze:
        with task_login(ServiceRole.WORKER, exact=True), work_transaction():
            demand = claim_rebuild(
                response_service.campaign.pk,
                "current",
                TaskClaim(status.run_id, status.fence, status.worker_id),
                admit=permit,
            )
        assert demand.claimed_generation_id is not None
    expire(status)
    with task_login(ServiceRole.WORKER, exact=True):
        assert recover_hint(
            root,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: fact_handler()},
        )
    assert TaskRun.objects.get(pk=root).state == "failed"
    assert produce() == ()
    assert TaskRun.objects.filter(task_type=TASK_TYPE).count() == 2
    if freeze:
        tomorrow = response_service.campaign.active_configuration.starts_at + timedelta(
            days=1
        )
        with patch(
            "parishkit.stewardship.reports.fact_production._now", return_value=tomorrow
        ):
            assert produce() == ()
        demand.refresh_from_db()
        assert demand.pending_revision == 2 and demand.pending_due_at is not None
    with work_transaction():
        retry = retry_failed(
            run_id=root,
            command_id=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=fact_handler().admit,
        )
    with task_login(ServiceRole.WORKER, exact=True):
        assert execute(retry.run_id)
    assert TaskRun.objects.get(pk=root).state == "failed"
    assert FactBuildReceipt.objects.filter(task_id=root).count() == 1
    if freeze:
        demand.refresh_from_db()
        assert demand.claimed_generation_id is None
        assert demand.pending_revision == 2 and demand.pending_due_at is not None
        with patch(
            "parishkit.stewardship.reports.fact_production._now", return_value=tomorrow
        ):
            assert len(produce()) == 1


@pytest.mark.parametrize("schedule_first", [False, True])
def test_new_revision_supersedes_only_unfrozen_terminal_root(
    response_service, schedule_first
):
    """An obsolete manual retry cannot race the newer revision in either order."""
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.storage import _status, retry_failed
    from parishkit.stewardship.reports.fact_production import hint_current_facts

    from .test_taskrun_postgresql import act

    root = produce()[1]
    failed = act(
        act(_status(TaskRun.objects.get(pk=root)), "claim"), "permanent_failure"
    )
    tomorrow = response_service.campaign.active_configuration.starts_at + timedelta(
        days=1
    )
    with patch(
        "parishkit.stewardship.reports.fact_production._now", return_value=tomorrow
    ):
        with work_transaction():
            instant = database_now() - timedelta(seconds=6)
            with patch(
                "parishkit.stewardship.reports.demand.database_now",
                return_value=instant,
            ):
                hint_current_facts(response_service.campaign.pk)
        replacements = produce() if schedule_first else ()
        with pytest.raises(PermissionError), work_transaction():
            retry_failed(
                run_id=failed.run_id,
                command_id=uuid4(),
                actor_id=uuid4(),
                correlation_id=uuid4(),
                admit=fact_handler().admit,
            )
        if not schedule_first:
            replacements = produce()
    assert len(replacements) == 1 and replacements[0] != root
    assert TaskRun.objects.filter(root_id=root).count() == 1
    with task_login(ServiceRole.WORKER, exact=True):
        assert execute(replacements[0])
    assert TaskRun.objects.get(pk=root).state == "failed"
    assert not FactBuildReceipt.objects.filter(task_id=root).exists()
    assert FactBuildReceipt.objects.filter(task_id=replacements[0]).exists()


def test_unfrozen_nonterminal_root_claims_latest_pending_inputs(response_service):
    """New events coalesce into queued work rather than making its key stale."""
    root = produce()[1]
    tomorrow = response_service.campaign.active_configuration.starts_at + timedelta(
        days=1
    )
    with patch(
        "parishkit.stewardship.reports.fact_production._now", return_value=tomorrow
    ):
        assert produce() == ()
    with task_login(ServiceRole.WORKER, exact=True):
        assert execute(root)
    record = CampaignDailyFactSet.objects.get()
    assert record.expected_count == 2
    assert FactBuildReceipt.objects.get(task_id=root).revision == 2


def test_restore_hold_fences_expired_work_without_spending_retry_budget(
    response_service,
):
    from parishkit.stewardship.jobs.dispatch import recover_hint
    from parishkit.stewardship.jobs.storage import _status

    from .campaign_builders import restored_runtime
    from .test_taskrun_postgresql import act, expire

    root = produce()[0]
    running = act(_status(TaskRun.objects.get(pk=root)), "claim", lease_seconds=1)
    expire(running)
    with transaction.atomic():
        instant = database_now()
    with restored_runtime(instant), task_login(ServiceRole.WORKER, exact=True):
        assert not recover_hint(
            root,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: fact_handler()},
        )
    run = TaskRun.objects.get(pk=root)
    assert run.state == "abandoned" and run.attempt == 1
    with task_login(ServiceRole.WORKER, exact=True):
        assert recover_hint(
            root,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: fact_handler()},
        )
    run.refresh_from_db()
    assert run.state == "retry_wait" and run.attempt == 1


def test_worker_cannot_add_an_orphan_fact_input_pin(response_service):
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.dispatch import claim_hint
    from parishkit.stewardship.source.pins import pin_snapshot

    roots = produce()
    root = next(
        root
        for root in roots
        if CampaignFactRebuildDemand.objects.get(
            pk=TaskRun.objects.get(pk=root).domain_request_id
        ).population_scope
        == "current"
    )
    with task_login(ServiceRole.WORKER, exact=True):
        claim_hint(
            root,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: fact_handler()},
        )
        with (
            pytest.raises(IntegrityError, match="exact retained builder"),
            work_transaction(),
        ):
            pin_snapshot(
                response_service.snapshot.pk,
                parent_kind="facts",
                parent_id=uuid4(),
                admit=permit,
            )


def test_worker_cannot_build_facts_with_an_unrelated_live_task(response_service):
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.ownership import TaskClaim
    from parishkit.stewardship.reports.demand import requested_inputs
    from parishkit.stewardship.reports.facts import begin_fact_set

    from .source_builders import running_source_task

    produce()
    demand = CampaignFactRebuildDemand.objects.get(population_scope="historical")
    values = running_source_task()
    claim = TaskClaim(values["task_id"], values["task_fence"], values["worker_id"])
    with (
        task_login(ServiceRole.WORKER, exact=True),
        pytest.raises(IntegrityError, match="not bound"),
        work_transaction(),
    ):
        begin_fact_set(requested_inputs(demand), claim, admit=permit)
    assert not CampaignDailyFactSet.objects.exists()
