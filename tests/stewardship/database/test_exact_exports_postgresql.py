"""Queued exact input protection, ownership and real downstream export execution."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.reports.exact_models import (
    ExactExportRequest,
    ExactExportResolution,
)
from parishkit.stewardship.reports.exact_services import (
    TASK_TYPE,
    cancel_exact_export,
    create_exact_export,
    exact_export_status,
)
from parishkit.stewardship.reports.exact_tasks import exact_handler
from parishkit.stewardship.reports.export_models import ExportPublication
from parishkit.stewardship.reports.export_tasks import export_handler, load_document
from parishkit.stewardship.reports.facts import fact_inputs
from parishkit.stewardship.reports.models import CampaignDailyFactSet, CampaignFactPin
from parishkit.stewardship.source.snapshot_models import SourceSnapshotPin

from .test_background_grants_postgresql import task_login
from .test_policy_postgresql import user

pytestmark = pytest.mark.django_db(transaction=True)


def request_exact(harness, principal, **options):
    """Capture current immutable inputs through the actual requester service."""
    return create_exact_export(
        harness.service.store,
        principal.pk,
        **(
            dict(
                campaign_id=harness.campaign.pk,
                population_scope="current",
                format="csv",
                browser_timezone="America/New_York",
                request_key=uuid4(),
            )
            | options
        ),
    )


def run_exact(harness, request):
    """Use the production dispatch, maintained lease and exact owner handler."""
    return execute_hint(
        request.task_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: exact_handler(store=harness.service.store)},
    )


@pytest.mark.parametrize("scope", ["historical", "current"])
def test_requester_and_worker_roles_handoff_one_exact_generation(
    response_service,
    tmp_path,
    monkeypatch,
    scope,
):
    harness = response_service
    principal = user("admin@example.org")
    with task_login(ServiceRole.WEB, exact=True):
        request = request_exact(harness, principal, population_scope=scope)
    assert not CampaignDailyFactSet.objects.exists()
    assert SourceSnapshotPin.objects.filter(
        snapshot=harness.snapshot,
        parent_kind="report",
        parent_id=request.pk,
    ).exists() == (scope == "current")
    with task_login(ServiceRole.WORKER, exact=True):
        assert run_exact(harness, request)
    resolution = ExactExportResolution.objects.get(request=request)
    assert fact_inputs(resolution.fact_set) == fact_inputs(request)
    assert CampaignFactPin.objects.filter(
        fact_set=resolution.fact_set,
        parent_kind="export",
        parent_id=resolution.export_id,
    ).exists()
    assert TaskRun.objects.get(pk=request.task_id).state == "succeeded"
    assert load_document(resolution.export).requested_at == request.created_at
    root = tmp_path / "reports"
    root.mkdir(mode=0o700)
    with task_login(ServiceRole.WORKER, reconnect=True):
        assert execute_hint(
            resolution.export.task_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={
                "report_export": export_handler(store=harness.service.store, root=root)
            },
        )
    assert ExportPublication.objects.get(request=resolution.export).row_count == 1
    status = exact_export_status(harness.service.store, principal.pk, request.pk)
    assert status["state"] == "ready"
    assert status["export_id"] == str(resolution.export_id)
    assert (
        status["expires_at"]
        == ExportPublication.objects.get(
            request=resolution.export
        ).expires_at.isoformat()
    )
    assert not run_exact(harness, request)
    publication = ExportPublication.objects.get(request=resolution.export)
    monkeypatch.setattr(
        "parishkit.stewardship.reports.export_services.database_now",
        lambda: publication.expires_at,
    )
    assert (
        exact_export_status(harness.service.store, principal.pk, request.pk)["state"]
        == "expired"
    )


def test_source_pin_is_required_in_same_commit(response_service, monkeypatch):
    from parishkit.stewardship.reports import exact_services

    monkeypatch.setattr(exact_services, "pin_snapshot", lambda *args, **kwargs: None)
    with pytest.raises(IntegrityError, match="retained source pin"):
        request_exact(response_service, user("admin@example.org"))
    assert not ExactExportRequest.objects.exists()
    assert not TaskRun.objects.filter(task_type=TASK_TYPE).exists()


def test_replay_does_not_recapture_newer_inputs(response_service, monkeypatch):
    from parishkit.stewardship.reports import exact_services

    principal = user("admin@example.org")
    request = request_exact(response_service, principal)

    def forbidden(*args):
        """Replaying an existing identity must not read a new source/date/watermark."""
        raise AssertionError("recaptured inputs")

    monkeypatch.setattr(exact_services, "current_inputs", forbidden)
    assert (
        request_exact(response_service, principal, request_key=request.request_key).pk
        == request.pk
    )
    with pytest.raises(ValueError, match="already bound"):
        request_exact(
            response_service, principal, request_key=request.request_key, format="pdf"
        )


def test_cancel_before_build_retains_inputs_without_allocating_generation(
    response_service,
):
    principal = user("admin@example.org")
    request = request_exact(response_service, principal)
    cancel_exact_export(response_service.service.store, principal.pk, request.pk)
    with task_login(ServiceRole.WORKER, exact=True):
        assert run_exact(response_service, request)
    assert TaskRun.objects.get(pk=request.task_id).state == "cancelled"
    assert not CampaignDailyFactSet.objects.exists()
    assert not ExactExportResolution.objects.exists()
    assert SourceSnapshotPin.objects.filter(
        parent_kind="report", parent_id=request.pk
    ).exists()


def test_resolution_without_owned_ready_generation_is_rejected(response_service):
    request = request_exact(response_service, user("admin@example.org"))
    with pytest.raises(IntegrityError), transaction.atomic():
        ExactExportResolution.objects.create(
            request=request,
            export_id=uuid4(),
            fact_set_id=uuid4(),
            run_id=request.task_id,
            fence=1,
            worker_id=uuid4(),
        )


def test_exact_priority_defers_only_unclaimed_ordinary_scope(response_service):
    from parishkit.stewardship.reports.models import CampaignFactRebuildDemand

    from .test_fact_tasks_postgresql import execute, produce

    roots = produce()
    ordinary = {
        CampaignFactRebuildDemand.objects.get(
            pk=TaskRun.objects.get(pk=root).domain_request_id
        ).population_scope: root
        for root in roots
    }
    request = request_exact(response_service, user("admin@example.org"))
    before = CampaignFactRebuildDemand.objects.get(population_scope="current")
    with pytest.raises(PermissionError, match="not admitted"):
        execute(ordinary["current"])
    assert execute(ordinary["historical"])
    assert run_exact(response_service, request)
    after = CampaignFactRebuildDemand.objects.get(pk=before.pk)
    assert after.pending_revision == before.pending_revision
    assert after.pending_due_at == before.pending_due_at
    assert after.claimed_generation_id is None
    assert execute(ordinary["current"])
    assert CampaignDailyFactSet.objects.filter(population_scope="current").count() == 1
