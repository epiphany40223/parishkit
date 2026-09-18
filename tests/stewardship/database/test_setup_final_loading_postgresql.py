"""Final setup loads selected giving afresh without publishing partial product state."""

# ruff: noqa: F811 -- imported fixtures are injected by pytest name.

from dataclasses import replace
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.accounts.setup_cancellation import cancel_finalizing_setup
from parishkit.stewardship.accounts.setup_install_models import SetupPreparationReceipt
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import Handler, claim_hint
from parishkit.stewardship.jobs.lifetime import maintain_execution
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.source.leases import acquire_source
from parishkit.stewardship.source.setup_final_loading import load_final_setup_source
from parishkit.stewardship.source.setup_final_scope import require_prepared_setup
from parishkit.stewardship.source.setup_final_tasks import (
    TASK_TYPE,
    bound_preparation,
    enqueue_finalization,
    finalization_admission,
)
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot
from parishkit.stewardship.storage import StorageInvariantError

from .setup_request_budgets import short_request_budgets
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_configuration_service_postgresql import (
    as_config_installer,
    config_role,  # noqa: F401
)
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_loading_postgresql import pages
from .test_setup_staging_postgresql import setup_service  # noqa: F401
from .test_setup_yaml_preparation_postgresql import install_inputs
from .test_source_refreshing_postgresql import fake_provider

pytestmark = pytest.mark.django_db(transaction=True)


def prepared(service, monkeypatch, tmp_path, *, financial=False):
    """Use real original setup, initial target installation and YAML preparation."""
    short_request_budgets(monkeypatch)
    if financial:
        from parishkit.stewardship.accounts.share_forms import default_share_options

        from ..campaign_factory import financial as periods
        from . import test_setup_preparation_postgresql as preparation

        original = preparation.first_campaign
        monkeypatch.setattr(
            preparation,
            "first_campaign",
            lambda *args: original(
                *args,
                modules=["census", "financial"],
                financial=periods(fund_duids=[9], comparison_fund_duids=[9]),
                share_options=default_share_options(),
            ),
        )
    browser, attempt, request = install_inputs(service, monkeypatch, tmp_path)
    with as_config_installer():
        install_request(
            service.store, request_id=request.request_id, correlation_id=uuid4()
        )
    return browser, attempt, SetupPreparationReceipt.objects.get().pk


def queued(service, identifier):
    """Exercise restricted scheduler intake and idempotency, not fabricated rows."""
    with task_login(ServiceRole.SCHEDULER, exact=True), work_transaction():
        task = enqueue_finalization(service.store, identifier, correlation_id=uuid4())
        assert (
            enqueue_finalization(service.store, identifier, correlation_id=uuid4())
            == task
        )
        return task


def wait_for_source():
    """Respect the real catalog HTTP exclusion without rewriting clock or fences."""
    from threading import Event

    from parishkit.stewardship.jobs.ownership import database_now
    from parishkit.stewardship.source.models import SourceMutationLease

    # The catalog's real HTTP deadline survives release. Never weaken that
    # exclusion by rewriting the database clock or bypassing admission in tests.
    with work_transaction():
        remaining = (
            SourceMutationLease.objects.get().external_deadline - database_now()
        ).total_seconds()
    assert remaining < 60
    if remaining > 0:
        Event().wait(remaining + 0.05)


def load(service, task, credential_path, *, finalize=None):
    """Use actual worker claim, renewal, page admission and staging transactions."""
    wait_for_source()
    handler = Handler(
        WorkQueue.GENERAL,
        finalization_admission(service.store),
        lambda _: None,
        scope=work_transaction,
    )
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        execution = claim_hint(
            task.run_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: handler},
        )
        assert execution is not None
        with maintain_execution(execution):
            with execution.effect():
                source = acquire_source(
                    task_id=execution.claim.run_id,
                    task_fence=execution.claim.fence,
                    worker_id=execution.claim.worker_id,
                    phase="full",
                )
            with execution.maintain_source(source):
                snapshot = load_final_setup_source(
                    execution,
                    source,
                    store=service.store,
                    credential_path=credential_path,
                )
            if finalize is not None:
                return finalize(execution, source, snapshot)
            # A caller which only stages has not performed atomic completion.
            with pytest.raises(PermissionError):
                execution.transition("complete")
            return snapshot


@pytest.mark.parametrize("financial", [False, True])
def test_final_load_has_new_fences_and_exact_selected_financial_coverage(
    setup_service, monkeypatch, tmp_path, config_role, financial
):
    """The initial catalog never masquerades as coverage for selected giving."""
    _, attempt, identifier = prepared(
        setup_service, monkeypatch, tmp_path, financial=financial
    )
    original = SourceSnapshot.objects.get()
    task = queued(setup_service, identifier)
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        scope = require_prepared_setup(setup_service.store, identifier)
    assert scope.attempt_id == attempt.attempt_id
    assert len(scope.window.periods) == (2 if financial else 0)
    responses = pages()
    if financial:
        from test_parishsoft_source import page

        from ..test_source_giving import contribution, pledge

        responses += [
            page([pledge(organizationID=1)]),
            page([contribution(organizationId=1, memberId=1)]),
        ]
    remaining, calls = fake_provider(monkeypatch, responses)
    snapshot = load(setup_service, task, tmp_path / "parishsoft" / "credential")
    assert not remaining and len(calls) == len(responses)
    assert snapshot.pk != original.pk and snapshot.task_id == task.run_id
    assert snapshot.source_fence > original.source_fence
    assert snapshot.cursor["window_digest"] == scope.window.digest
    assert (
        snapshot.counts["pledge"] == snapshot.counts["contribution"] == int(financial)
    )
    assert SourceCurrent.objects.get().snapshot_id is None
    original.refresh_from_db()
    assert original.state == snapshot.state == "ready"
    assert original.counts["pledge"] == original.counts["contribution"] == 0
    assert TaskRun.objects.get(pk=task.run_id).state == "running"
    assert SetupAttempt.objects.get().state == "frozen"
    assert not setup_service.configured()


def test_wrong_mounted_key_prevents_final_provider_reads(
    setup_service, monkeypatch, tmp_path, config_role
):
    """A preparation receipt cannot authorize a different installed credential."""
    _, _, identifier = prepared(setup_service, monkeypatch, tmp_path)
    task = queued(setup_service, identifier)
    path = tmp_path / "parishsoft" / "credential"
    write_private(path, b"different-synthetic-key")
    _, calls = fake_provider(monkeypatch, [])
    with pytest.raises(PermissionError, match="credential"):
        load(setup_service, task, path)
    assert not calls and SourceSnapshot.objects.count() == 1


def test_cancelled_preparation_cannot_enqueue_or_read_under_new_authority(
    setup_service, monkeypatch, tmp_path, config_role
):
    """The immutable handoff remains history, not permission after original cancel."""
    browser, attempt, identifier = prepared(setup_service, monkeypatch, tmp_path)
    with web_login():
        cancel_finalizing_setup(browser, setup_service, attempt.attempt_id)
    with (
        task_login(ServiceRole.SCHEDULER, exact=True),
        work_transaction(),
        pytest.raises(PermissionError, match="no longer live"),
    ):
        enqueue_finalization(setup_service.store, identifier, correlation_id=uuid4())
    assert not TaskRun.objects.filter(task_type=TASK_TYPE).exists()


@pytest.mark.parametrize("role", [ServiceRole.WORKER, ServiceRole.SCHEDULER])
def test_preparation_reads_do_not_grant_mutation_or_foreign_private_context(role):
    """Public handoff reads add neither configuration authority nor provider keys."""
    with task_login(role, exact=True), connection.cursor() as cursor:
        cursor.execute("SELECT id FROM stewardship_setup_prepared")
    for statement in (
        "INSERT INTO stewardship_setup_prepared DEFAULT VALUES",
        "SELECT settings FROM stewardship_provider_context",
        "SELECT ciphertext FROM stewardship_setup_sealed_credential",
    ):
        with (
            task_login(role, exact=True),
            pytest.raises(DatabaseError),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)


def test_final_task_binding_refuses_stale_or_catalog_status(
    setup_service, monkeypatch, tmp_path, config_role
):
    """Status snapshots cannot rebind an immutable root to another setup."""
    _, _, identifier = prepared(setup_service, monkeypatch, tmp_path)
    task = queued(setup_service, identifier)
    with task_login(ServiceRole.WORKER, exact=True), work_transaction():
        assert bound_preparation(task).pk == task.run_id
        for invalid in (
            replace(task, version=task.version + 1),
            replace(task, domain_request_id=uuid4()),
            replace(task, task_type="setup_source_load"),
        ):
            with pytest.raises(PermissionError):
                bound_preparation(invalid)
    with pytest.raises(StorageInvariantError):
        require_prepared_setup(setup_service.store, identifier)


@pytest.mark.parametrize("invalid", ["missing", "old_manifest", "manifest_race"])
def test_selected_preparation_is_not_a_reusable_authority_token(
    setup_service, monkeypatch, tmp_path, config_role, invalid
):
    """Every finalization admission repeats receipt and both manifest observations."""
    _, _, identifier = prepared(setup_service, monkeypatch, tmp_path)
    if invalid == "missing":
        identifier = uuid4()
    elif invalid == "old_manifest":
        base = setup_service.store.read_version(SetupAttempt.objects.get().base_id)
        setup_service.store.select(base)
    else:
        monkeypatch.setattr(
            setup_service.store, "manifest_reference", lambda: (uuid4(), "f" * 64)
        )
    with (
        task_login(ServiceRole.WORKER, exact=True),
        work_transaction(),
        pytest.raises((PermissionError, ConfigError)),
    ):
        require_prepared_setup(setup_service.store, identifier)


def test_original_cancel_between_final_pages_stops_staging(
    setup_service, monkeypatch, tmp_path, config_role
):
    """Cancellation while HTTP is outside SQL closes the next page and all effects."""
    import json

    from parishkit import parishsoft_transport

    browser, attempt, identifier = prepared(setup_service, monkeypatch, tmp_path)
    task = queued(setup_service, identifier)
    calls = []

    def exchange(payload, **kwargs):
        """Commit the real original-browser cancellation during one synthetic page."""
        assert not connection.in_atomic_block
        calls.append(payload)
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
        try:
            with web_login():
                cancel_finalizing_setup(browser, setup_service, attempt.attempt_id)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION AUTHORIZATION pk_stewardship_worker")
        return b"200\n" + json.dumps(pages()[0]).encode()

    monkeypatch.setattr(parishsoft_transport, "_exchange", exchange)
    with pytest.raises(PermissionError):
        load(setup_service, task, tmp_path / "parishsoft" / "credential")
    assert len(calls) == 1
    snapshot = SourceSnapshot.objects.get(task_id=task.run_id)
    assert snapshot.state == "staging" and snapshot.counts == {}
    assert SetupAttempt.objects.get().state == "expired"
    assert SourceCurrent.objects.get().snapshot_id is None
    assert not setup_service.configured()
