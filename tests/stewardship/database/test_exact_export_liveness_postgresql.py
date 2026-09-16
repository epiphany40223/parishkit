"""Revoked/cancelled exact owners must not strand shared report calculations."""

from uuid import uuid4

import pytest
from django.db import transaction

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import recover_hint
from parishkit.stewardship.jobs.models import NONTERMINAL_STATES, TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scanning import collect_hints
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports.exact_models import ExactExportResolution
from parishkit.stewardship.reports.exact_services import (
    TASK_TYPE,
    cancel_exact_export,
    exact_export_status,
    retry_exact_export,
)
from parishkit.stewardship.reports.exact_tasks import exact_handler
from parishkit.stewardship.reports.models import (
    CampaignDailyFactSet,
    CampaignFactRebuildDemand,
    FactBuildReceipt,
)

from ..policy_factory import address
from .campaign_builders import restored_runtime
from .test_background_grants_postgresql import task_login
from .test_exact_export_recovery_postgresql import interrupt_build, terminal
from .test_exact_exports_postgresql import request_exact, run_exact
from .test_export_authorization_postgresql import add_policy
from .test_fact_tasks_postgresql import execute, produce
from .test_policy_postgresql import user
from .test_taskrun_postgresql import act, expire

pytestmark = pytest.mark.django_db(transaction=True)


def ordinary_current():
    """Find the producer's real due current-scope root."""
    return next(
        root
        for root in produce()
        if CampaignFactRebuildDemand.objects.get(
            pk=TaskRun.objects.get(pk=root).domain_request_id
        ).population_scope
        == "current"
    )


def abandon(request):
    """Shorten a genuine interrupted lease, then expire it through guarded storage."""
    with work_transaction():
        status = act(
            _status(TaskRun.objects.get(pk=request.task_id)),
            "heartbeat",
            lease_seconds=1,
        )
    with work_transaction():
        expire(status)


@pytest.mark.parametrize("consumer", ["exact", "ordinary"])
def test_revoked_abandoned_owner_releases_key_to_authorized_consumer(
    response_service, monkeypatch, consumer
):
    harness = response_service
    admin = user("admin@example.org")
    add_policy(
        (harness.service.store, admin, None, None),
        address("staff@example.org", ("staff",)),
    )
    staff = user("staff@example.org")
    first = request_exact(harness, staff)
    facts = interrupt_build(harness, first, monkeypatch)
    abandon(first)
    staff.disabled = True
    staff.version += 1
    staff.save()
    with task_login(ServiceRole.SCHEDULER, exact=True):
        hints, _ = collect_hints(handlers={TASK_TYPE: exact_handler(scheduler=True)})
        assert [hint.run_id for hint in hints] == [first.task_id]
    with task_login(ServiceRole.WORKER, exact=True):
        assert recover_hint(
            first.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: exact_handler(store=harness.service.store)},
        )
    assert TaskRun.objects.get(pk=first.task_id).state == "failed"
    assert (
        exact_export_status(harness.service.store, admin.pk, first.pk)["state"]
        == "failed"
    )
    with task_login(ServiceRole.SCHEDULER, exact=True):
        hints, _ = collect_hints(handlers={TASK_TYPE: exact_handler(scheduler=True)})
        assert not hints
    if consumer == "exact":
        second = request_exact(harness, admin)
        with task_login(ServiceRole.WORKER, exact=True):
            assert run_exact(harness, second)
        assert ExactExportResolution.objects.get(request=second).fact_set_id == facts.pk
    else:
        root = ordinary_current()
        with task_login(ServiceRole.WORKER, exact=True):
            assert execute(root)
        assert FactBuildReceipt.objects.get(task_id=root).fact_set_id == facts.pk
    facts.refresh_from_db()
    assert facts.state == "ready"
    assert not ExactExportResolution.objects.filter(request=first).exists()
    assert TaskRun.objects.get(pk=first.task_id).state == "failed"


@pytest.mark.parametrize("action", ["permanent_failure", "safe_cancel"])
def test_ordinary_can_resume_terminal_exact_generation(
    response_service, monkeypatch, action
):
    first = request_exact(response_service, user("admin@example.org"))
    facts = interrupt_build(response_service, first, monkeypatch)
    terminal(first, action)
    root = ordinary_current()
    with task_login(ServiceRole.WORKER, exact=True):
        assert execute(root)
    assert FactBuildReceipt.objects.get(task_id=root).fact_set_id == facts.pk
    assert not ExactExportResolution.objects.exists()
    assert (
        CampaignFactRebuildDemand.objects.get(
            population_scope="current"
        ).claimed_generation_id
        is None
    )


def test_held_abandoned_request_has_no_repeated_recovery_hints(
    response_service, monkeypatch
):
    first = request_exact(response_service, user("admin@example.org"))
    interrupt_build(response_service, first, monkeypatch)
    abandon(first)
    with transaction.atomic():
        instant = database_now()
    with restored_runtime(instant), task_login(ServiceRole.SCHEDULER, exact=True):
        hints, _ = collect_hints(handlers={TASK_TYPE: exact_handler(scheduler=True)})
        assert not hints
    with task_login(ServiceRole.SCHEDULER, exact=True):
        hints, _ = collect_hints(handlers={TASK_TYPE: exact_handler(scheduler=True)})
        assert [hint.run_id for hint in hints] == [first.task_id]


def test_cancellation_during_calculation_finishes_without_waiting_for_lease(
    response_service, monkeypatch
):
    from parishkit.stewardship.reports import materialization

    principal = user("admin@example.org")
    request = request_exact(response_service, principal)
    original = materialization.calculate_participation

    def cancel_before_staging(context):
        """Commit cancellation between the actual read and next chunk effect."""
        result = original(context)
        cancel_exact_export(response_service.service.store, principal.pk, request.pk)
        return result

    monkeypatch.setattr(
        materialization, "calculate_participation", cancel_before_staging
    )
    assert run_exact(response_service, request)
    assert TaskRun.objects.get(pk=request.task_id).state == "cancelled"
    assert not ExactExportResolution.objects.exists()
    assert CampaignDailyFactSet.objects.get().state == "building"


@pytest.mark.parametrize("state", ["queued", "running", "abandoned", "retry_wait"])
def test_ordinary_sql_cannot_take_a_nonterminal_exact_checkpoint(
    response_service, monkeypatch, state
):
    """Even a bypassed Python callback cannot steal a recoverable exact root."""
    from django.db import IntegrityError

    from parishkit.stewardship.jobs.dispatch import claim_hint
    from parishkit.stewardship.reports.fact_tasks import fact_handler

    principal = user("admin@example.org")
    request = request_exact(response_service, principal)
    facts = interrupt_build(response_service, request, monkeypatch)
    if state == "abandoned":
        abandon(request)
    elif state == "queued":
        terminal(request)
        retry_exact_export(
            response_service.service.store,
            principal.pk,
            request.pk,
            request_key=uuid4(),
        )
    elif state == "retry_wait":
        with work_transaction():
            act(_status(TaskRun.objects.get(pk=request.task_id)), "retryable_failure")
    assert list(
        TaskRun.objects.filter(
            root_id=request.task_id, state__in=NONTERMINAL_STATES
        ).values_list("state", flat=True)
    ) == [state]
    # Revocation removes priority, but does not authorize stealing an unfenced
    # checkpoint. Recovery must first close the former task under its root lock.
    principal.disabled = True
    principal.version += 1
    principal.save()
    root = ordinary_current()
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_hint(
            root,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"report_facts": fact_handler()},
        )
        with (
            pytest.raises(IntegrityError, match="frozen root"),
            work_transaction(),
        ):
            # Write directly so every parameter reaches the SQL ownership guard,
            # rather than being rejected by the Python live-builder check.
            facts.task_id = execution.claim.run_id
            facts.task_fence = execution.claim.fence
            facts.worker_id = execution.claim.worker_id
            facts.version += 1
            facts.save()
    facts.refresh_from_db()
    assert facts.task_id == request.task_id


def test_cancellation_after_staging_keeps_reusable_checkpoint(
    response_service, monkeypatch
):
    from parishkit.stewardship.reports import materialization

    principal = user("admin@example.org")
    request = request_exact(response_service, principal)
    original = materialization.stage_fact_days

    def cancel_after_chunk(*args, **kwargs):
        """Commit a real staged chunk and cancellation before publication."""
        result = original(*args, **kwargs)
        cancel_exact_export(response_service.service.store, principal.pk, request.pk)
        return result

    with monkeypatch.context() as changed:
        changed.setattr(materialization, "stage_fact_days", cancel_after_chunk)
        assert run_exact(response_service, request)
    facts = CampaignDailyFactSet.objects.get()
    day_ids = list(facts.days.values_list("id", flat=True))
    assert facts.state == "building" and day_ids
    assert TaskRun.objects.get(pk=request.task_id).state == "cancelled"
    root = ordinary_current()
    with task_login(ServiceRole.WORKER, exact=True):
        assert execute(root)
    facts.refresh_from_db()
    assert facts.state == "ready"
    assert list(facts.days.values_list("id", flat=True)) == day_ids
    assert not ExactExportResolution.objects.exists()


def test_noncancellation_permission_failure_is_not_acknowledged(
    response_service, monkeypatch
):
    from parishkit.stewardship.reports import exact_tasks

    request = request_exact(response_service, user("admin@example.org"))

    def denied(*args, **kwargs):
        """An admission refusal without a cancellation is still a real failure."""
        raise PermissionError("synthetic unrelated admission refusal")

    monkeypatch.setattr(exact_tasks, "materialize_fact_set", denied)
    with pytest.raises(PermissionError, match="unrelated admission"):
        run_exact(response_service, request)
    assert TaskRun.objects.get(pk=request.task_id).state == "running"
    assert not ExactExportResolution.objects.exists()


def test_revocation_during_calculation_does_not_claim_cancellation(
    response_service, monkeypatch
):
    from parishkit.stewardship.reports import materialization

    principal = user("admin@example.org")
    request = request_exact(response_service, principal)
    original = materialization.calculate_participation

    def revoke(context):
        """Revoke the real identity between calculation and its first write."""
        result = original(context)
        principal.disabled = True
        principal.version += 1
        principal.save()
        return result

    monkeypatch.setattr(materialization, "calculate_participation", revoke)
    # Identity lookup currently raises DoesNotExist; a future consistent
    # admission-denial translation must preserve the same non-cancel outcome.
    with pytest.raises((type(principal).DoesNotExist, PermissionError)):
        run_exact(response_service, request)
    assert TaskRun.objects.get(pk=request.task_id).state == "running"
    assert not ExactExportResolution.objects.exists()
