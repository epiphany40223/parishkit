"""Cancelled finalization retains safe history, not either load's temporary PII."""

# ruff: noqa: F811 -- imported fixtures are injected by pytest name.

from threading import Event
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.setup_cancellation import cancel_finalizing_setup
from parishkit.stewardship.accounts.setup_install_models import SetupPreparationReceipt
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.ownership import database_now
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.source.models import SourceMutationLease
from parishkit.stewardship.source.setup_cleanup import (
    cleanup_handler,
    produce_setup_cleanup,
)
from parishkit.stewardship.source.setup_disposal import owned_snapshots
from parishkit.stewardship.source.snapshot_models import SourceCurrent, SourceSnapshot
from parishkit.stewardship.source.version_models import ENTITY_MODELS

from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_configuration_service_postgresql import (
    as_config_installer,
    config_role,  # noqa: F401
)
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_final_loading_postgresql import load, prepared, queued
from .test_setup_loading_postgresql import pages
from .test_setup_staging_postgresql import setup_service  # noqa: F401
from .test_source_refreshing_postgresql import fake_provider

pytestmark = pytest.mark.django_db(transaction=True)


def cleanup(service, browser, attempt, task):
    """Cancel through real owners and wait for actual task/source/HTTP drainage."""
    with web_login():
        cancel_finalizing_setup(browser, service, attempt.attempt_id)
    receipt = SetupPreparationReceipt.objects.select_related("readiness__intent").get()
    with as_config_installer():
        result = install_request(
            service.store,
            request_id=receipt.readiness.intent.request_id,
            correlation_id=uuid4(),
        )
        assert result.state == "failed"
    with work_transaction():
        lease = SourceMutationLease.objects.get()
        final = TaskRun.objects.get(pk=task.run_id)
        deadlines = [lease.external_deadline, lease.expires_at, final.lease_expires_at]
        remaining = max(
            (deadline - database_now()).total_seconds()
            for deadline in deadlines
            if deadline is not None
        )
    assert remaining < 90
    if remaining > 0:
        Event().wait(remaining + 0.05)
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        identifiers = produce_setup_cleanup(guard)
        assert len(identifiers) == 1
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            identifiers[0],
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"setup_source_cleanup": cleanup_handler()},
        )
    assert TaskRun.objects.get(pk=identifiers[0]).state == "succeeded"
    assert TaskRun.objects.get(pk=task.run_id).state == "cancelled"


@pytest.mark.parametrize("loaded", [False, True])
def test_cancel_cleans_catalog_and_its_final_load_but_keeps_bound_receipts(
    setup_service, monkeypatch, tmp_path, config_role, loaded
):
    """Both queued and abandoned-after-staging finalization have a real cleanup path."""
    browser, attempt, identifier = prepared(setup_service, monkeypatch, tmp_path)
    task = queued(setup_service, identifier)
    if loaded:
        fake_provider(monkeypatch, pages())
        load(setup_service, task, tmp_path / "parishsoft" / "credential")
    assert SourceSnapshot.objects.count() == (2 if loaded else 1)
    cleanup(setup_service, browser, attempt, task)
    with work_transaction():
        assert owned_snapshots(attempt.attempt_id).count() == (2 if loaded else 1)
    assert SourceSnapshot.objects.filter(state="rejected").count() == (
        2 if loaded else 1
    )
    for payload, membership in ENTITY_MODELS.values():
        assert not payload.objects.exists() and not membership.objects.exists()
    assert SetupPreparationReceipt.objects.get().pk == identifier
    assert SetupAttempt.objects.get().state == "expired"
    assert SourceCurrent.objects.get().snapshot_id is None
    assert not setup_service.configured()


def test_final_cleanup_guard_reverses_and_reapplies_on_empty_history():
    """The frozen predecessor remains recoverable before finalization Tasks exist."""
    from importlib import import_module

    migration = import_module(
        "parishkit.stewardship.source.migrations.0022_setup_final_source_cleanup"
    )
    operation = migration.Migration.operations[0]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef("
            "'public.stewardship_setup_disposable_snapshot_v1(uuid)'::regprocedure)"
        )
        original = cursor.fetchone()[0]
        try:
            cursor.execute(operation.reverse_sql)
            cursor.execute(
                "SELECT pg_get_functiondef("
                "'public.stewardship_setup_disposable_snapshot_v1(uuid)'::regprocedure)"
            )
            assert "setup_finalize" not in cursor.fetchone()[0]
        finally:
            cursor.execute(original)
        cursor.execute(
            "SELECT pg_get_functiondef("
            "'public.stewardship_setup_disposable_snapshot_v1(uuid)'::regprocedure)"
        )
        assert "setup_finalize" in cursor.fetchone()[0]


def test_final_task_history_prevents_removing_its_only_cleanup_owner(
    setup_service, monkeypatch, tmp_path, config_role
):
    """Even queued finalization history cannot be downgraded into stranded staging."""
    from importlib import import_module

    migration = import_module(
        "parishkit.stewardship.source.migrations.0022_setup_final_source_cleanup"
    )
    _, _, identifier = prepared(setup_service, monkeypatch, tmp_path)
    queued(setup_service, identifier)
    with (
        pytest.raises(DatabaseError, match="prevents cleanup downgrade"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(migration.Migration.operations[0].reverse_sql)
