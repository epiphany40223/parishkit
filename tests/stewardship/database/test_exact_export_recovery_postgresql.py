"""Claim gaps, frozen recovery, cancellation and atomic handoff regression tests."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.storage import _status
from parishkit.stewardship.reports.exact_models import ExactExportResolution
from parishkit.stewardship.reports.exact_services import TASK_TYPE, retry_exact_export
from parishkit.stewardship.reports.exact_tasks import exact_handler
from parishkit.stewardship.reports.models import CampaignDailyFactSet

from .campaign_builders import campaign_clock
from .test_background_grants_postgresql import task_login
from .test_exact_exports_postgresql import request_exact, run_exact
from .test_fact_tasks_postgresql import produce
from .test_policy_postgresql import user
from .test_taskrun_postgresql import act

pytestmark = pytest.mark.django_db(transaction=True)


def claim_exact(harness, request):
    """Stop at the real post-commit, pre-generation dispatch gap."""
    return claim_hint(
        request.task_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: exact_handler(store=harness.service.store)},
    )


def interrupt_build(harness, request, monkeypatch):
    """Leave genuine fenced generation ownership behind after a calculation crash."""
    from parishkit.stewardship.reports import materialization

    def fail(*args):
        """Simulate local process failure before any publication."""
        raise RuntimeError("synthetic exact calculation interruption")

    with monkeypatch.context() as changed:
        changed.setattr(materialization, "calculate_participation", fail)
        with pytest.raises(RuntimeError, match="synthetic exact"):
            run_exact(harness, request)
    return CampaignDailyFactSet.objects.get()


def terminal(request, action="permanent_failure"):
    """Fixture control closes a real owned run using guarded TaskRun transitions."""
    with work_transaction():
        return act(_status(TaskRun.objects.get(pk=request.task_id)), action)


def test_identical_claims_cannot_both_win_pre_allocation_gap(response_service):
    principal = user("admin@example.org")
    first = request_exact(response_service, principal)
    second = request_exact(response_service, principal)
    with task_login(ServiceRole.WORKER, exact=True):
        assert claim_exact(response_service, first) is not None
        with pytest.raises(PermissionError):
            claim_exact(response_service, second)
    assert not CampaignDailyFactSet.objects.exists()
    assert TaskRun.objects.get(pk=second.task_id).state == "queued"


def test_exact_claim_waits_for_ordinary_claim_allocation_gap(response_service):
    from parishkit.stewardship.reports.fact_tasks import fact_handler
    from parishkit.stewardship.reports.models import CampaignFactRebuildDemand

    root = next(
        root
        for root in produce()
        if CampaignFactRebuildDemand.objects.get(
            pk=TaskRun.objects.get(pk=root).domain_request_id
        ).population_scope
        == "current"
    )
    assert (
        claim_hint(
            root,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"report_facts": fact_handler()},
        )
        is not None
    )
    request = request_exact(response_service, user("admin@example.org"))
    with pytest.raises(PermissionError):
        claim_exact(response_service, request)
    assert TaskRun.objects.get(pk=request.task_id).state == "queued"


def test_retry_keeps_date_source_and_watermark_after_clock_advances(
    response_service,
    monkeypatch,
):
    principal = user("admin@example.org")
    request = request_exact(response_service, principal)
    original = interrupt_build(response_service, request, monkeypatch)
    terminal(request)
    with campaign_clock(
        response_service.campaign.active_configuration.starts_at + timedelta(days=1)
    ):
        with task_login(ServiceRole.WEB, exact=True):
            retry = retry_exact_export(
                response_service.service.store,
                principal.pk,
                request.pk,
                request_key=uuid4(),
            )
        with task_login(ServiceRole.WORKER, exact=True):
            from parishkit.stewardship.jobs.dispatch import execute_hint

            assert execute_hint(
                retry.run_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={
                    TASK_TYPE: exact_handler(store=response_service.service.store)
                },
            )
    resolution = ExactExportResolution.objects.get(request=request)
    assert resolution.fact_set_id == original.pk
    assert resolution.fact_set.through_date == request.through_date
    assert resolution.fact_set.source_id == request.source_id
    assert resolution.fact_set.submission_watermark == request.submission_watermark
    assert resolution.fact_set.task_id == retry.run_id


@pytest.mark.parametrize("action", ["permanent_failure", "safe_cancel"])
def test_terminal_exact_builder_can_be_resumed_without_reopening_its_request(
    response_service,
    monkeypatch,
    action,
):
    principal = user("admin@example.org")
    first = request_exact(response_service, principal)
    facts = interrupt_build(response_service, first, monkeypatch)
    terminal(first, action)
    second = request_exact(response_service, principal)
    with task_login(ServiceRole.WORKER, exact=True):
        assert run_exact(response_service, second)
    assert ExactExportResolution.objects.get(request=second).fact_set_id == facts.pk
    assert not ExactExportResolution.objects.filter(request=first).exists()
    assert TaskRun.objects.get(pk=first.task_id).state in {"failed", "cancelled"}


def test_failed_handoff_rolls_back_child_pin_and_receipt(response_service, monkeypatch):
    from parishkit.stewardship.reports import exact_tasks
    from parishkit.stewardship.reports.export_models import ExportRequest
    from parishkit.stewardship.reports.models import CampaignFactPin

    request = request_exact(response_service, user("admin@example.org"))
    monkeypatch.setattr(exact_tasks, "pin_facts", lambda *args, **kwargs: None)
    with pytest.raises(IntegrityError, match="retained fact pin"):
        run_exact(response_service, request)
    assert CampaignDailyFactSet.objects.get().state == "ready"
    assert not ExportRequest.objects.exists()
    assert not ExactExportResolution.objects.exists()
    assert not CampaignFactPin.objects.exists()
    assert not TaskRun.objects.filter(task_type="report_export").exists()
    assert TaskRun.objects.get(pk=request.task_id).state == "running"


def test_retained_request_pin_cannot_be_expired_or_deleted(response_service):
    from django.db import connection

    from parishkit.stewardship.source.snapshot_models import SourceSnapshotPin

    request = request_exact(response_service, user("admin@example.org"))
    pin = SourceSnapshotPin.objects.get(parent_id=request.pk, parent_kind="report")
    for statement in (
        "DELETE FROM stewardship_source_pin WHERE id=%s",
        "UPDATE stewardship_source_pin SET expires_at=clock_timestamp(),"
        "version=version+1 WHERE id=%s",
    ):
        with (
            pytest.raises(IntegrityError, match="Retained exact export still requires"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, (pin.pk,))


def test_exact_claim_first_blocks_ordinary_allocation_gap(response_service):
    """The reverse ordering keeps ordinary work queued until exact work resolves."""
    from parishkit.stewardship.jobs.dispatch import maintain_execution
    from parishkit.stewardship.reports.models import CampaignFactRebuildDemand

    from .test_fact_tasks_postgresql import execute

    root = next(
        root
        for root in produce()
        if CampaignFactRebuildDemand.objects.get(
            pk=TaskRun.objects.get(pk=root).domain_request_id
        ).population_scope
        == "current"
    )
    request = request_exact(response_service, user("admin@example.org"))
    with task_login(ServiceRole.WORKER, exact=True):
        execution = claim_exact(response_service, request)
        with pytest.raises(PermissionError, match="not admitted"):
            execute(root)
        assert not CampaignDailyFactSet.objects.exists()
        with maintain_execution(execution):
            execution.handler.execute(execution)
        assert execute(root)
    assert CampaignDailyFactSet.objects.count() == 1


def test_ordinary_retry_after_exact_claim_is_a_waiting_dependency(
    response_service,
    monkeypatch,
):
    """An ordinary retry can regain its frozen checkpoint after the exact claim."""
    from parishkit.stewardship.jobs.dispatch import maintain_execution
    from parishkit.stewardship.jobs.storage import retry_failed
    from parishkit.stewardship.reports import materialization
    from parishkit.stewardship.reports.fact_tasks import fact_handler
    from parishkit.stewardship.reports.models import CampaignFactRebuildDemand

    from .test_fact_tasks_postgresql import execute

    root = next(
        root
        for root in produce()
        if CampaignFactRebuildDemand.objects.get(
            pk=TaskRun.objects.get(pk=root).domain_request_id
        ).population_scope
        == "current"
    )

    def fail(*args):
        """Leave the ordinary builder's exact checkpoint recoverable."""
        raise RuntimeError("synthetic ordinary interruption")

    with monkeypatch.context() as changed:
        changed.setattr(materialization, "calculate_participation", fail)
        with pytest.raises(RuntimeError, match="ordinary interruption"):
            execute(root)
    with work_transaction():
        failed = act(_status(TaskRun.objects.get(pk=root)), "permanent_failure")
    request = request_exact(response_service, user("admin@example.org"))
    execution = claim_exact(response_service, request)
    with work_transaction():
        retry = retry_failed(
            run_id=failed.run_id,
            command_id=uuid4(),
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=fact_handler().admit,
        )
    with task_login(ServiceRole.WORKER, exact=True), maintain_execution(execution):
        execution.handler.execute(execution)
    assert TaskRun.objects.get(pk=request.task_id).state == "retry_wait"
    assert not ExactExportResolution.objects.exists()
    assert execute(retry.run_id)
    assert CampaignDailyFactSet.objects.get().state == "ready"


def test_cancelled_failed_request_cannot_allocate_another_retry(response_service):
    """Retry reports a conflict instead of allocating a doomed cancellation run."""
    from parishkit.stewardship.jobs.storage import TaskRetryConflict
    from parishkit.stewardship.reports.exact_services import cancel_exact_export

    principal = user("admin@example.org")
    request = request_exact(response_service, principal)
    claim_exact(response_service, request)
    terminal(request)
    cancel_exact_export(response_service.service.store, principal.pk, request.pk)
    with pytest.raises(TaskRetryConflict, match="Cancelled"):
        retry_exact_export(
            response_service.service.store,
            principal.pk,
            request.pk,
            request_key=uuid4(),
        )
    assert request.task.chain_runs.count() == 1


def test_recovery_waits_quietly_while_campaign_gate_is_closed(
    response_service, monkeypatch
):
    """Domain holds do not become recovery errors or new retry budget windows."""
    from dataclasses import replace

    from parishkit.stewardship.reports import exact_tasks

    request = request_exact(response_service, user("admin@example.org"))
    status = _status(request.task)

    def hold(*args, **kwargs):
        """Represent an actual lifecycle admission refusal, not a DB failure."""
        raise PermissionError("synthetic campaign hold")

    monkeypatch.setattr(exact_tasks, "bound_request", lambda value: request)
    monkeypatch.setattr(exact_tasks, "admit_campaign", hold)
    assert exact_tasks.recover_exact(replace(status, state="abandoned")) is None


def test_retry_after_handoff_stays_with_the_renderer_root(response_service):
    """A failed renderer retry cannot reopen or duplicate the completed parent."""
    principal = user("admin@example.org")
    request = request_exact(response_service, principal)
    assert run_exact(response_service, request)
    resolution = ExactExportResolution.objects.get(request=request)
    with work_transaction():
        act(act(_status(resolution.export.task), "claim"), "permanent_failure")
    key = uuid4()
    with task_login(ServiceRole.WEB, exact=True):
        retry = retry_exact_export(
            response_service.service.store, principal.pk, request.pk, request_key=key
        )
        assert (
            retry_exact_export(
                response_service.service.store,
                principal.pk,
                request.pk,
                request_key=key,
            ).run_id
            == retry.run_id
        )
    assert TaskRun.objects.get(pk=retry.run_id).root_id == resolution.export.task_id
    assert request.task.chain_runs.count() == 1
    assert TaskRun.objects.get(pk=request.task_id).state == "succeeded"
