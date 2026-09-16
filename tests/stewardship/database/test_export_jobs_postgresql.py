"""Requester scope, pinned input and actual compiled export-worker integration."""

from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction

from parishkit.stewardship.accounts.authority import AuthorityStore
from parishkit.stewardship.accounts.configuration_schema import validate_sections
from parishkit.stewardship.campaigns.credential_models import CampaignCredentialState
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.reports.artifacts import ArtifactReceipt, open_artifact
from parishkit.stewardship.reports.export_models import ExportPublication, ExportRequest
from parishkit.stewardship.reports.export_services import (
    TASK_TYPE,
    cancel_export,
    consume_download,
    create_export,
    export_status,
    issue_download,
)
from parishkit.stewardship.reports.export_tasks import export_handler
from parishkit.stewardship.reports.facts import FactUnavailable, publish_fact_set
from parishkit.stewardship.reports.models import CampaignFactPin

from .fact_builders import fact_fixture, staged_facts
from .test_background_grants_postgresql import task_login
from .test_policy_postgresql import user
from .test_runtime_auth_grants_postgresql import web_login
from .test_source_snapshots_postgresql import permit

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def scenario(tmp_path):
    """Use real configuration/source/fact publication, without live providers."""
    inputs, owner, source = fact_fixture(tmp_path)
    CampaignCredentialState.objects.get_or_create(campaign_id=inputs.campaign_id)
    facts, _ = staged_facts(inputs, owner, source)
    publish_fact_set(facts.pk, owner, admit=permit)
    store = AuthorityStore(tmp_path, validate_sections)
    principal = user("admin@example.org")
    root = tmp_path / "reports"
    root.mkdir(mode=0o700)
    return store, principal, facts, root


def request_export(scenario, **options):
    """Submit one complete closed-format request with a fresh idempotency key."""
    store, principal, facts, _ = scenario
    values = dict(
        campaign_id=facts.campaign_id,
        fact_set_id=facts.pk,
        format="csv",
        browser_timezone="America/New_York",
        request_key=uuid4(),
    )
    return create_export(store, principal.pk, **(values | options))


def run_export(scenario, request):
    """Exercise real dispatch ownership, lifetime and compiled renderer."""
    store, _, _, root = scenario
    return execute_hint(
        request.task_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: export_handler(store=store, root=root)},
    )


def test_request_pins_inputs_and_replay_cannot_change_them(scenario):
    """Allocation is atomic and repeats neither the task nor the generation pin."""
    request = request_export(scenario)
    assert request_export(scenario, request_key=request.request_key).pk == request.pk
    assert (
        CampaignFactPin.objects.get(parent_kind="export").fact_set_id
        == request.fact_set_id
    )
    assert TaskRun.objects.filter(task_type=TASK_TYPE).count() == 1
    with pytest.raises(ValueError, match="already bound"):
        request_export(scenario, request_key=request.request_key, format="pdf")
    with pytest.raises(FactUnavailable):
        request_export(scenario, fact_set_id=uuid4())
    assert ExportRequest.objects.count() == 1


def test_missing_pin_rolls_back_request_and_task(scenario, monkeypatch):
    """Deferred SQL completeness prevents committing unpinned work."""
    from parishkit.stewardship.reports import export_services

    monkeypatch.setattr(export_services, "pin_facts", lambda *args, **kwargs: None)
    with pytest.raises(IntegrityError, match="retained fact pin"):
        request_export(scenario)
    assert not ExportRequest.objects.exists()
    assert not TaskRun.objects.filter(task_type=TASK_TYPE).exists()


@pytest.mark.parametrize(
    "options",
    [
        {"format": "html"},
        {"format": ["csv"]},
        {"browser_timezone": "private-invalid-zone"},
        {"request_key": "not-a-uuid"},
    ],
)
def test_closed_request_contract_precedes_allocation(scenario, options):
    """Untrusted values cannot select arbitrary renderers, queries or storage paths."""
    with pytest.raises(ValueError):
        request_export(scenario, **options)
    assert not ExportRequest.objects.exists()


@pytest.mark.parametrize("format", ["csv", "png", "pdf"])
def test_real_worker_publishes_complete_artifact_then_one_use_download(
    scenario, format
):
    """All formats follow the same authorized claim/query/publication path."""
    store, principal, facts, root = scenario
    request = request_export(scenario, format=format)
    assert run_export(scenario, request)
    publication = ExportPublication.objects.get(request=request)
    assert publication.row_count == facts.expected_count
    assert TaskRun.objects.get(pk=request.task_id).state == "succeeded"
    assert export_status(store, principal.pk, request.pk)["state"] == "ready"
    with open_artifact(
        root,
        request.campaign_id,
        ArtifactReceipt(publication.attempt_id, publication.size, publication.sha256),
    ) as stream:
        value = stream.read()
    assert value.startswith(
        {"csv": b"date,scope,", "png": b"\x89PNG", "pdf": b"%PDF"}[format]
    )
    grant = issue_download(store, principal.pk, request.pk)
    assert consume_download(store, principal.pk, grant.pk).pk == publication.pk
    with pytest.raises(IntegrityError), transaction.atomic():
        consume_download(store, principal.pk, grant.pk)
    assert not run_export(scenario, request)


def test_cancelled_queued_work_never_renders(scenario):
    """An immutable cancellation is safe worker completion, not an arbitrary kill."""
    store, principal, _, root = scenario
    request = request_export(scenario)
    cancel_export(store, principal.pk, request.pk)
    assert run_export(scenario, request)
    assert not ExportPublication.objects.exists()
    assert not (root / "exports").exists()
    assert TaskRun.objects.get(pk=request.task_id).state == "cancelled"
    assert export_status(store, principal.pk, request.pk)["state"] == "cancelled"


def test_unallowlisted_identity_cannot_create_read_cancel_or_download(scenario):
    """A guessed UUID and a verified Google identity do not imply report access."""
    store, _, facts, _ = scenario
    outsider = user("outsider@example.org")
    with pytest.raises(PermissionError):
        create_export(
            store,
            outsider.pk,
            campaign_id=facts.campaign_id,
            fact_set_id=facts.pk,
            format="csv",
            browser_timezone="UTC",
            request_key=uuid4(),
        )
    request = request_export(scenario)
    for operation in (export_status, cancel_export, issue_download):
        with pytest.raises(PermissionError):
            operation(store, outsider.pk, request.pk)


def test_real_web_worker_and_scheduler_sql_grants(scenario):
    """Operational roles own the whole positive path, not just mocked admission."""
    from parishkit.stewardship.jobs.scanning import collect_hints

    store, principal, _, _ = scenario
    with web_login():
        request = request_export(scenario)
    with task_login(ServiceRole.SCHEDULER):
        handler = export_handler(scheduler=True)
        hints, _ = collect_hints(handlers={TASK_TYPE: handler})
        assert [hint.run_id for hint in hints] == [request.task_id]
        with pytest.raises(PermissionError):
            handler.execute(None)
    with task_login(ServiceRole.WORKER):
        assert run_export(scenario, request)
    with web_login():
        grant = issue_download(store, principal.pk, request.pk)
        publication = consume_download(store, principal.pk, grant.pk)
        assert publication.request_id == request.pk


def test_revocation_after_request_prevents_worker_query(scenario):
    """Disabled original requesters cannot retain queued report authority."""
    _, principal, _, root = scenario
    request = request_export(scenario)
    principal.disabled = True
    principal.version += 1
    principal.save()
    with pytest.raises(type(principal).DoesNotExist):
        run_export(scenario, request)
    assert not (root / "exports").exists()
    assert not ExportPublication.objects.exists()


def test_cancel_wins_publication_after_rendering(scenario, monkeypatch):
    """A complete temporary file is never permission to publish a cancelled job."""
    from parishkit.stewardship.reports import export_tasks

    store, principal, _, _ = scenario
    request = request_export(scenario)
    original = export_tasks.write_artifact

    def after_render(*args):
        """Use a second connection because rendering owns a read-only transaction."""
        from concurrent.futures import ThreadPoolExecutor

        from django.db import connections

        receipt = original(*args)

        def cancel_current():
            """Commit cancellation and close this test-owned connection."""
            try:
                cancel_export(store, principal.pk, request.pk)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(cancel_current).result(timeout=10)
        return receipt

    monkeypatch.setattr(export_tasks, "write_artifact", after_render)
    assert run_export(scenario, request)
    assert not ExportPublication.objects.exists()
    assert TaskRun.objects.get(pk=request.task_id).state == "cancelled"
