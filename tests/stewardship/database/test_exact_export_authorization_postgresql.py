"""Fresh policy, later inputs, metadata scanning and retained report ownership."""

from uuid import uuid4

import pytest
from django.db import connection

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.scanning import collect_hints
from parishkit.stewardship.reports.exact_models import ExactExportResolution
from parishkit.stewardship.reports.exact_services import (
    TASK_TYPE,
    cancel_exact_export,
    exact_export_status,
)
from parishkit.stewardship.reports.exact_tasks import exact_handler
from parishkit.stewardship.reports.export_models import ExportCancellation
from parishkit.stewardship.reports.export_tasks import load_document
from parishkit.stewardship.reports.models import CampaignDailyFactSet

from ..policy_factory import address
from .test_background_grants_postgresql import task_login
from .test_exact_exports_postgresql import request_exact, run_exact
from .test_export_authorization_postgresql import add_policy
from .test_fact_materialization_postgresql import respond
from .test_fact_tasks_postgresql import execute, produce
from .test_policy_postgresql import user

pytestmark = pytest.mark.django_db(transaction=True)


def test_current_cutoff_does_not_follow_later_live_response(live_response_service):
    harness = live_response_service
    request = request_exact(harness, user("admin@example.org"))
    assert request.submission_watermark == 0
    assert respond(harness).campaign_sequence == 1
    assert run_exact(harness, request)
    facts = ExactExportResolution.objects.get(request=request).fact_set
    assert facts.submission_watermark == 0
    assert list(facts.days.values_list("cumulative_responses", flat=True)) == [0]


def test_handoff_preserves_request_configuration_after_policy_revision(
    response_service,
):
    harness = response_service
    principal = user("admin@example.org")
    request = request_exact(harness, principal)
    add_policy(
        (harness.service.store, principal, None, None),
        address("new-staff@example.org", ("staff",)),
    )
    with task_login(ServiceRole.WORKER, exact=True):
        assert run_exact(harness, request)
    export = ExactExportResolution.objects.get(request=request).export
    assert export.configuration_id == request.configuration_id
    assert load_document(export).requested_at == request.created_at


def test_unrelated_staff_cannot_read_or_cancel_another_request(response_service):
    harness = response_service
    admin = user("admin@example.org")
    add_policy(
        (harness.service.store, admin, None, None),
        address("staff@example.org", ("staff",)),
        address("other@example.org", ("staff",)),
    )
    staff, other = user("staff@example.org"), user("other@example.org")
    request = request_exact(harness, staff)
    assert (
        exact_export_status(harness.service.store, admin.pk, request.pk)["state"]
        == "queued"
    )
    for service in (exact_export_status, cancel_exact_export):
        with pytest.raises(PermissionError):
            service(harness.service.store, other.pk, request.pk)


def test_scheduler_can_hint_exact_work_but_cannot_execute(response_service):
    request = request_exact(response_service, user("admin@example.org"))
    with task_login(ServiceRole.SCHEDULER, exact=True):
        handler = exact_handler(scheduler=True)
        hints, _ = collect_hints(handlers={TASK_TYPE: handler})
        assert [hint.run_id for hint in hints] == [request.task_id]
        with pytest.raises(PermissionError, match="scheduler"):
            handler.execute(None)


def test_revoked_exact_request_does_not_starve_ordinary_work(response_service):
    principal = user("admin@example.org")
    request = request_exact(response_service, principal)
    principal.disabled = True
    principal.version += 1
    principal.save()
    with task_login(ServiceRole.SCHEDULER, exact=True):
        hints, _ = collect_hints(handlers={TASK_TYPE: exact_handler(scheduler=True)})
        assert not hints
    with pytest.raises(type(principal).DoesNotExist):
        run_exact(response_service, request)
    for root in produce():
        assert execute(root)
    assert CampaignDailyFactSet.objects.count() == 2


def test_cancel_after_handoff_uses_existing_export_outcome(response_service):
    principal = user("admin@example.org")
    request = request_exact(response_service, principal)
    run_exact(response_service, request)
    export = ExactExportResolution.objects.get(request=request).export
    cancel_exact_export(response_service.service.store, principal.pk, request.pk)
    assert ExportCancellation.objects.filter(request=export).exists()
    assert (
        exact_export_status(response_service.service.store, principal.pk, request.pk)[
            "state"
        ]
        == "cancelled"
    )


def test_ready_matching_request_prevents_compaction_before_handoff(
    response_service, monkeypatch
):
    from parishkit.stewardship.reports import exact_tasks

    request = request_exact(response_service, user("admin@example.org"))

    def interrupted(*args):
        """Leave ready facts before handoff to test retained input protection."""
        raise RuntimeError("synthetic handoff interruption")

    monkeypatch.setattr(exact_tasks, "_handoff", interrupted)
    with pytest.raises(RuntimeError, match="handoff interruption"):
        run_exact(response_service, request)
    facts = CampaignDailyFactSet.objects.get()
    from parishkit.stewardship.reports.materialization import materialize_fact_set

    from .test_fact_materialization_postgresql import allocation
    from .test_source_snapshots_postgresql import permit

    newer, owner = allocation(response_service, population="current")
    materialize_fact_set(newer.pk, owner, admit=permit)
    assert newer.through_date > facts.through_date
    with work_transaction(), connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_fact_disposable(%s)", (facts.pk,))
        assert cursor.fetchone() == (False,)


@pytest.mark.parametrize("bad", ["format", "scope", "timezone", "identity"])
def test_invalid_intent_allocates_no_request(response_service, bad):
    from parishkit.stewardship.reports.exact_models import ExactExportRequest

    options = {
        "format": {"format": "html"},
        "scope": {"population_scope": []},
        "timezone": {"browser_timezone": "private-value"},
        "identity": {"request_key": str(uuid4())},
    }[bad]
    with pytest.raises(ValueError):
        request_exact(response_service, user("admin@example.org"), **options)
    assert not ExactExportRequest.objects.exists()
