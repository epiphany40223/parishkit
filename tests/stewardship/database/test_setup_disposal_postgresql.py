"""Expired setup disposal never grants general source-payload deletion."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from threading import Event
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction

from parishkit.stewardship.accounts.setup_staging import cancel_setup
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import Execution, Handler
from parishkit.stewardship.jobs.ownership import TaskClaim, database_now
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.setup_disposal import dispose_batch
from parishkit.stewardship.source.snapshot_models import SourceSnapshot
from parishkit.stewardship.source.version_models import ENTITY_MODELS

from .setup_request_budgets import short_request_budgets
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_loading_postgresql import pages, prepared, queued, run
from .test_setup_staging_postgresql import setup_service  # noqa: F401
from .test_source_refreshing_postgresql import fake_provider
from .test_taskrun_postgresql import act, new

pytestmark = pytest.mark.django_db(transaction=True)


def cleanup_claim(attempt_id):
    """Synthetic queue metadata isolates the production deletion guards under test."""
    task = act(
        new(task_type="setup_source_cleanup", domain_request_id=attempt_id),
        "claim",
        lease_seconds=300,
    )
    claim = TaskClaim(task.run_id, task.fence, task.worker_id)
    source = acquire_source(
        task_id=task.run_id,
        task_fence=task.fence,
        worker_id=task.worker_id,
        phase="full",
        lease_seconds=300,
    )
    execution = Execution(
        claim,
        Handler(
            WorkQueue.GENERAL, lambda *_: True, lambda _: None, scope=work_transaction
        ),
        uuid4(),
    )
    return execution, source


def test_real_worker_removes_only_expired_setup_rows(setup_service, monkeypatch):
    """PII disappears but immutable source and setup result receipts survive."""
    short_request_budgets(monkeypatch)
    values, request = prepared(setup_service, with_request=True)
    fake_provider(monkeypatch, pages())
    snapshot = run(*values, complete=True)
    from parishkit.stewardship.accounts.setup_models import SetupAttempt

    attempt = SetupAttempt.objects.get()
    with web_login():
        cancel_setup(request, setup_service, attempt.pk)
    from parishkit.stewardship.source.models import SourceMutationLease

    with transaction.atomic():
        remaining = (
            SourceMutationLease.objects.get().external_deadline - database_now()
        ).total_seconds()
    assert 0 < remaining < 60
    # Preserve the real finite provider drain, rather than rewriting lease clocks
    # or disabling guards. The scheduler must likewise wait before disposal.
    Event().wait(remaining + 0.05)
    execution, source = cleanup_claim(attempt.pk)
    removed = 0
    with task_login(ServiceRole.WORKER, exact=True):
        while True:
            with execution.effect():
                count = dispose_batch(execution, source)
            if count is None:
                break
            removed += count
        with execution.effect():
            assert dispose_batch(execution, source) is None
    assert removed == sum(snapshot.counts.values())
    assert SourceSnapshot.objects.get().state == "rejected"
    for payload, membership in ENTITY_MODELS.values():
        assert not payload.objects.exists()
        assert not membership.objects.exists()
    release_source(source)


def test_live_setup_payload_delete_is_denied_even_to_worker(setup_service, monkeypatch):
    """DELETE grants cannot remove a live or ready source even with a valid lease."""
    values = prepared(setup_service)
    fake_provider(monkeypatch, pages())
    run(*values)
    with task_login(ServiceRole.WORKER, exact=True):
        for table in ("stewardship_source_family", "stewardship_snapshot_family"):
            with (
                pytest.raises(DatabaseError) as raised,
                transaction.atomic(),
                connection.cursor() as c,
            ):
                c.execute(f"DELETE FROM {table}")
            assert raised.value.__cause__.sqlstate == "23514"
    assert SourceSnapshot.objects.get().state == "ready"


def test_scheduler_queues_once_and_worker_cancels_unstarted_load(setup_service):
    """No-data cancellation still closes original queued work without provider I/O."""
    from parishkit.stewardship.jobs.dispatch import execute_hint
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.scheduler import scheduler_session
    from parishkit.stewardship.source.setup_cleanup import (
        cleanup_handler,
        produce_setup_cleanup,
    )

    request, attempt, original, _ = queued(setup_service)
    with web_login():
        cancel_setup(request, setup_service, attempt.attempt_id)
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        identifiers = produce_setup_cleanup(guard)
        assert len(identifiers) == 1
        assert produce_setup_cleanup(guard) == ()
    with task_login(ServiceRole.WORKER, exact=True, reconnect=True):
        assert execute_hint(
            identifiers[0],
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={"setup_source_cleanup": cleanup_handler()},
        )
    assert TaskRun.objects.get(pk=identifiers[0]).state == "succeeded"
    assert TaskRun.objects.get(pk=original.run_id).state == "cancelled"


def staged_rows(service, count=501):
    """Create synthetic interrupted staging through real append/ownership guards.

    No provider request occurs, so no external reservation needs to be skipped
    or shortened. Full HTTP/drain integration is covered separately above.
    """
    from parishkit.stewardship.source.snapshots import begin_snapshot, stage_entities

    values, request = prepared(service, with_request=True)
    execution, source, recipient, _ = values
    with work_transaction():
        snapshot = begin_snapshot(source, organization_id=1, admit=lambda *_: True)
        for start in range(0, count, 500):
            stage_entities(
                snapshot.pk,
                source,
                kind="family",
                entities={
                    str(index): {"name": "synthetic-staged-family"}
                    for index in range(start, min(start + 500, count))
                },
                admit=lambda *_: True,
            )
    release_source(source)
    with web_login():
        cancel_setup(request, service, recipient.public.scope.attempt_id)
    return snapshot, recipient.public.scope.attempt_id


def test_bounded_cleanup_rollback_and_retry_preserve_all_references(setup_service):
    """A failed batch restores payloads and membership; the next owner can resume."""
    _, attempt_id = staged_rows(setup_service)
    execution, source = cleanup_claim(attempt_id)
    payload, membership = ENTITY_MODELS["family"]
    with task_login(ServiceRole.WORKER, exact=True):
        with (
            pytest.raises(RuntimeError, match="synthetic rollback"),
            execution.effect(),
        ):
            assert dispose_batch(execution, source) == 500
            raise RuntimeError("synthetic rollback")
        assert payload.objects.count() == membership.objects.count() == 501
        with execution.effect():
            assert dispose_batch(execution, source) == 500
        assert payload.objects.count() == membership.objects.count() == 1
        with execution.effect():
            assert dispose_batch(execution, source) == 1
        with execution.effect():
            assert dispose_batch(execution, source) is None
    assert not payload.objects.exists() and not membership.objects.exists()


def test_another_source_snapshot_keeps_its_shared_payload(setup_service):
    """Exact cleanup must preserve versions referenced outside the expired setup."""
    from parishkit.stewardship.source.snapshots import begin_snapshot, stage_entities

    _, attempt_id = staged_rows(setup_service, count=1)
    other = act(new(task_type="source_refresh"), "claim", lease_seconds=300)
    other_claim = acquire_source(
        task_id=other.run_id,
        task_fence=other.fence,
        worker_id=other.worker_id,
        phase="full",
        lease_seconds=300,
    )
    with work_transaction():
        other_snapshot = begin_snapshot(
            other_claim, organization_id=1, admit=lambda *_: True
        )
        stage_entities(
            other_snapshot.pk,
            other_claim,
            kind="family",
            entities={"0": {"name": "synthetic-staged-family"}},
            admit=lambda *_: True,
        )
    release_source(other_claim)
    execution, source = cleanup_claim(attempt_id)
    payload, membership = ENTITY_MODELS["family"]
    with task_login(ServiceRole.WORKER, exact=True):
        with execution.effect():
            assert dispose_batch(execution, source) == 1
        with (
            pytest.raises(DatabaseError) as raised,
            execution.effect(),
            connection.cursor() as c,
        ):
            c.execute("DELETE FROM stewardship_source_family")
        assert raised.value.__cause__.sqlstate == "23514"
        assert payload.objects.count() == membership.objects.count() == 1
        assert membership.objects.get().snapshot_id == other_snapshot.pk
