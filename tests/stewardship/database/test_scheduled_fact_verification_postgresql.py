"""Daily checks exercise real task ownership and SQL roles, not a fake scheduler."""

from dataclasses import replace
from datetime import UTC, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import IntegrityError, ProgrammingError, connection, transaction

from parishkit.stewardship.audit.models import AuditEvent, OperationalLog
from parishkit.stewardship.campaigns.models import CampaignConfiguration
from parishkit.stewardship.campaigns.runtime import _now
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import (
    Execution,
    claim_hint,
    execute_hint,
    recover_hint,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.storage import _status, enqueue, retry_failed
from parishkit.stewardship.reports.facts import (
    begin_fact_set,
    fact_inputs,
    publish_fact_set,
)
from parishkit.stewardship.reports.materialization import materialize_fact_set
from parishkit.stewardship.reports.models import CampaignDailyFactSet
from parishkit.stewardship.reports.retention import compact_facts, pin_facts
from parishkit.stewardship.reports.verification_models import (
    FactVerificationRequest,
    FactVerificationResult,
)
from parishkit.stewardship.reports.verification_production import (
    INPUT_FIELDS,
    TASK_TYPE,
    produce_verifications,
)
from parishkit.stewardship.reports.verification_tasks import verification_handler

from .campaign_builders import campaign_clock, restored_runtime
from .fact_builders import staged_facts
from .test_background_grants_postgresql import task_login
from .test_fact_materialization_postgresql import allocation
from .test_fact_tasks_postgresql import execute as build
from .test_fact_tasks_postgresql import produce as produce_builds
from .test_source_snapshots_postgresql import permit
from .test_taskrun_postgresql import act, expire

pytestmark = pytest.mark.django_db(transaction=True)


def ready():
    """Build both real scopes with ordinary task receipts and pointers."""
    for root in produce_builds():
        assert build(root)


def produce(**options):
    """Use actual scheduler ownership, including the canonical advisory mutex."""
    with scheduler_session() as guard:
        return produce_verifications(guard, **options)


def execute(root):
    """Dispatch through the real handler and independently maintained heartbeat."""
    return execute_hint(
        root,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: verification_handler()},
    )


def test_real_daily_producer_and_worker_match_both_scopes(response_service):
    ready()
    with task_login(ServiceRole.SCHEDULER, exact=True):
        roots = produce()
        assert len(roots) == 2
        assert produce() == ()
        with pytest.raises(PermissionError, match="scheduler"):
            verification_handler(scheduler=True).execute(None)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        for root in roots:
            assert execute(root)
            assert not execute(root)
            with connection.cursor() as cursor:
                cursor.execute("SELECT session_user")
                assert cursor.fetchone() == ("pk_stewardship_worker",)
    assert FactVerificationResult.objects.count() == 2
    assert set(FactVerificationResult.objects.values_list("outcome", flat=True)) == {
        "matched"
    }
    assert set(
        TaskRun.objects.filter(pk__in=roots).values_list("state", flat=True)
    ) == {"succeeded"}
    assert produce() == ()


def test_drift_is_distinct_and_does_not_rewrite_facts(response_service):
    facts, claim = allocation(response_service, population="current")
    staged_facts(fact_inputs(facts), claim, response_service.snapshot)
    publish_fact_set(facts.pk, claim, admit=permit)
    root = produce(limit=1)[0]
    before = list(CampaignDailyFactSet.objects.values())
    request = FactVerificationRequest.objects.get(task_id=root)
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute(root)
    result = FactVerificationResult.objects.get(request=request)
    assert result.outcome == "drift" and result.differing_days == facts.expected_count
    assert TaskRun.objects.get(pk=root).state == "succeeded"
    assert list(CampaignDailyFactSet.objects.values()) == before
    log = OperationalLog.objects.get(event="fact_drift")
    assert log.level == "CRITICAL"
    assert log.context == {
        "task_id": str(root),
        "count": facts.expected_count,
        "outcome": "failed",
    }
    audit = AuditEvent.objects.get(event_type="facts_verified", subject_id=result.pk)
    assert audit.campaign_reference == request.campaign_id
    assert audit.parish_id == CampaignConfiguration.objects.values_list(
        "configuration__parish__id", flat=True
    ).get(pk=request.timezone_configuration_id)
    assert audit.auditcontext.context == {
        "count": facts.expected_count,
        "outcome": "succeeded",
    }


@pytest.mark.parametrize(
    "role,table",
    [(ServiceRole.SCHEDULER, "result"), (ServiceRole.WORKER, "request")],
)
def test_runtime_roles_cannot_insert_other_owners_evidence(
    response_service, role, table
):
    """Real database grants reject cross-owner inserts before row validation."""
    with task_login(role, exact=True):
        with (
            pytest.raises(ProgrammingError) as denied,
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(
                f"INSERT INTO stewardship_fact_verification_{table} DEFAULT VALUES"
            )
        assert denied.value.__cause__.sqlstate == "42501"


def test_restore_hold_does_not_allocate_checks(response_service):
    """A producer tick during restore cannot allocate work for retained facts."""
    ready()
    with transaction.atomic():
        instant = database_now()
    with restored_runtime(instant):
        assert produce() == ()
    assert not FactVerificationRequest.objects.exists()


@pytest.mark.parametrize("pinned", [False, True])
def test_daily_selection_skips_only_disposable_generations(response_service, pinned):
    """Current and durably pinned inputs remain checked; obsolete caches do not."""
    old, claim = allocation(response_service, population="current")
    materialize_fact_set(old.pk, claim, admit=permit, interactive=True)
    newer = begin_fact_set(
        replace(fact_inputs(old), through_date=old.through_date + timedelta(days=1)),
        claim,
        admit=permit,
    )
    materialize_fact_set(newer.pk, claim, admit=permit, interactive=True)
    if pinned:
        pin_facts(old.pk, parent_kind="export", parent_id=uuid4(), admit=permit)
    with task_login(ServiceRole.SCHEDULER, exact=True):
        assert len(produce()) == (2 if pinned else 1)
    assert set(
        FactVerificationRequest.objects.values_list("fact_set_id", flat=True)
    ) == ({old.pk, newer.pk} if pinned else {newer.pk})
    if not pinned:
        # Bypass candidate selection: SQL must reject creating protection for
        # disposable rows even when a valid queued task was allocated first.
        def allocate():
            """Keep every input identical except the durable retention pin."""
            # response_service freezes the campaign clock for both controls.
            assert _now() == response_service.campaign.active_configuration.starts_at
            identifier = uuid4()
            task = enqueue(
                task_type=TASK_TYPE,
                domain_request_id=identifier,
                idempotency_key=identifier,
                actor_id=None,
                correlation_id=identifier,
                admit=permit,
            )
            FactVerificationRequest.objects.create(
                id=identifier,
                task_id=task.root_id,
                fact_set_id=old.pk,
                scheduled_day=_now().astimezone(UTC).date(),
                correlation_id=identifier,
                **{field: getattr(old, field) for field in INPUT_FIELDS},
            )
            return task.root_id

        with (
            pytest.raises(IntegrityError, match="daily task binding"),
            task_login(ServiceRole.SCHEDULER, exact=True),
            work_transaction(),
        ):
            allocate()
        pin_facts(old.pk, parent_kind="operator", parent_id=uuid4(), admit=permit)
        with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
            root = allocate()
        assert FactVerificationRequest.objects.get(task_id=root).fact_set_id == old.pk


def test_result_cannot_be_forged_before_claim(response_service):
    ready()
    root = produce(limit=1)[0]
    request = FactVerificationRequest.objects.get(task_id=root)
    worker = uuid4()
    with pytest.raises(IntegrityError, match="live fenced"), transaction.atomic():
        FactVerificationResult.objects.create(
            request=request,
            run_id=root,
            fence=1,
            worker_id=worker,
            actor_id=worker,
            outcome="matched",
            differing_days=0,
        )
    assert not FactVerificationResult.objects.exists()


def test_bounded_daily_cadence_does_not_supersede_unfinished_work(response_service):
    ready()
    roots = produce(limit=1)
    assert len(roots) == 1
    assert len(produce(limit=1)) == 1
    assert produce() == ()
    request = FactVerificationRequest.objects.get(task_id=roots[0])
    with work_transaction():
        tomorrow = _now() + timedelta(days=1)
    with campaign_clock(tomorrow):
        # An old queued check still owns its generation despite the new day.
        assert produce() == ()
        assert execute(roots[0])
        next_day = produce()
        assert len(next_day) == 1
        next_request = FactVerificationRequest.objects.get(task_id=next_day[0])
        assert next_request.fact_set_id == request.fact_set_id
        assert next_request.scheduled_day > request.scheduled_day
        assert produce() == ()


def test_interruption_and_atomic_acknowledgment_can_retry_original_inputs(
    response_service,
):
    ready()
    root = produce(limit=1)[0]
    original = Execution.transition

    def interrupted(self, action, **options):
        """Fail after result insertion; its enclosing transaction must roll back."""
        if action == "complete":
            raise RuntimeError("synthetic acknowledgment loss")
        return original(self, action, **options)

    with (
        patch.object(Execution, "transition", interrupted),
        pytest.raises(RuntimeError),
    ):
        execute(root)
    assert not FactVerificationResult.objects.exists()
    assert TaskRun.objects.get(pk=root).state == "running"
    with work_transaction():
        failed = act(_status(TaskRun.objects.get(pk=root)), "permanent_failure")
        retry = retry_failed(
            run_id=failed.run_id,
            command_id=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=verification_handler().admit,
        )
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute(retry.run_id)
    result = FactVerificationResult.objects.get()
    assert result.request.task_id == root and result.run_id == retry.run_id
    assert result.outcome == "matched"


def test_failed_or_unavailable_calculation_is_never_a_clean_result(response_service):
    from parishkit.stewardship.reports.facts import FactUnavailable

    ready()
    root = produce(limit=1)[0]
    with (
        patch(
            "parishkit.stewardship.reports.verification_tasks.verify_fact_set",
            side_effect=FactUnavailable("synthetic unavailable source"),
        ),
        pytest.raises(FactUnavailable),
    ):
        execute(root)
    assert not FactVerificationResult.objects.exists()
    assert TaskRun.objects.get(pk=root).state == "running"


def test_recovery_waits_for_restore_release_then_retries(response_service):
    ready()
    root = produce(limit=1)[0]
    status = act(_status(TaskRun.objects.get(pk=root)), "claim", lease_seconds=1)
    expire(status)
    with transaction.atomic():
        instant = database_now()
    with restored_runtime(instant), work_transaction():
        assert (
            verification_handler().recover(_status(TaskRun.objects.get(pk=root)))
            is None
        )
        assert not verification_handler().admit(
            "recovery_hint", _status(TaskRun.objects.get(pk=root))
        )
    with task_login(ServiceRole.WORKER, exact=True):
        assert recover_hint(
            root,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: verification_handler()},
        )
    assert TaskRun.objects.get(pk=root).state == "retry_wait"
    assert not FactVerificationResult.objects.exists()


def test_queued_check_protects_superseded_facts_only_until_completion(response_service):
    old, claim = allocation(response_service, population="current")
    materialize_fact_set(old.pk, claim, admit=permit, interactive=True)
    root = produce(limit=1)[0]
    newer = begin_fact_set(
        replace(fact_inputs(old), through_date=old.through_date + timedelta(days=1)),
        claim,
        admit=permit,
    )
    materialize_fact_set(newer.pk, claim, admit=permit, interactive=True)
    assert compact_facts(old.campaign_id, claim, admit=permit) == []
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute(root)
    assert compact_facts(old.campaign_id, claim, admit=permit) == [old.pk]
    request = FactVerificationRequest.objects.get(task_id=root)
    assert request.fact_set_id == old.pk
    assert FactVerificationResult.objects.get(request=request).outcome == "matched"


@pytest.mark.parametrize("table", ["request", "result"])
def test_verification_evidence_is_immutable(response_service, table):
    ready()
    root = produce(limit=1)[0]
    execute(root)
    model = FactVerificationRequest if table == "request" else FactVerificationResult
    record = model.objects.first()
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            f"UPDATE stewardship_fact_verification_{table} SET actor_id=%s WHERE id=%s",
            (uuid4(), record.pk),
        )
    with (
        pytest.raises(IntegrityError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(
            f"DELETE FROM stewardship_fact_verification_{table} WHERE id=%s",
            (record.pk,),
        )


def test_result_insert_requires_atomic_task_acknowledgment(response_service):
    ready()
    root = produce(limit=1)[0]
    execution = claim_hint(
        root,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: verification_handler()},
    )
    with pytest.raises(IntegrityError, match="acknowledgment"), work_transaction():
        FactVerificationResult.objects.create(
            request=FactVerificationRequest.objects.get(task_id=root),
            run_id=root,
            fence=execution.claim.fence,
            worker_id=execution.claim.worker_id,
            actor_id=execution.claim.worker_id,
            outcome="matched",
            differing_days=0,
        )
    assert not FactVerificationResult.objects.exists()


def test_compacted_terminal_request_cannot_retry_or_switch_generation(response_service):
    old, claim = allocation(response_service, population="current")
    materialize_fact_set(old.pk, claim, admit=permit, interactive=True)
    root = produce(limit=1)[0]
    failed = act(
        act(_status(TaskRun.objects.get(pk=root)), "claim"), "permanent_failure"
    )
    newer = begin_fact_set(
        replace(fact_inputs(old), through_date=old.through_date + timedelta(days=1)),
        claim,
        admit=permit,
    )
    materialize_fact_set(newer.pk, claim, admit=permit, interactive=True)
    assert compact_facts(old.campaign_id, claim, admit=permit) == [old.pk]
    options = dict(
        run_id=failed.run_id,
        command_id=uuid4(),
        actor_id=uuid4(),
        correlation_id=uuid4(),
    )
    with pytest.raises(PermissionError), work_transaction():
        retry_failed(**options, admit=verification_handler().admit)
    # Bypassing the Python admission callback still cannot allocate a root for
    # absent inputs: the deferred SQL owner guard independently rejects it.
    with pytest.raises(IntegrityError, match="available unowned"), work_transaction():
        retry_failed(**options, admit=permit)
    assert TaskRun.objects.filter(root_id=root).count() == 1


def test_exhausted_crash_recovery_is_critical_and_does_not_reset_same_day(
    response_service,
):
    ready()
    root = produce(limit=1)[0]
    status = _status(TaskRun.objects.get(pk=root))
    for _ in range(4):
        status = act(act(status, "claim"), "retryable_failure")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(1.05)")
    status = act(status, "claim", lease_seconds=1)
    expire(status)
    with task_login(ServiceRole.WORKER, exact=True):
        assert recover_hint(
            root,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: verification_handler()},
        )
    assert TaskRun.objects.get(pk=root).state == "failed"
    assert (
        OperationalLog.objects.filter(
            event="task_failed", level="CRITICAL", context__task_id=str(root)
        ).count()
        == 1
    )
    assert not FactVerificationResult.objects.exists()
    original = FactVerificationRequest.objects.get(task_id=root)
    produce()
    assert (
        FactVerificationRequest.objects.filter(fact_set_id=original.fact_set_id).count()
        == 1
    )
