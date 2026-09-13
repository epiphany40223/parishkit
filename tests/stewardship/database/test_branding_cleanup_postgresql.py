"""Real scheduler/worker roles own expiry cleanup without changing retained logos."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from parishkit.config import ConfigError
from parishkit.stewardship.accounts import branding_cleanup as cleanup
from parishkit.stewardship.accounts.branding_files import create_bundle
from parishkit.stewardship.accounts.configuration_requests import record_request
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import claim_hint, execute_hint, recover_hint
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.jobs.storage import _status, change_run, retry_failed
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import change, initialized, restored_runtime
from .test_background_grants_postgresql import task_login
from .test_branding_postgresql import advance, branding_patch, ready
from .test_branding_setup_postgresql import graphics

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """Real files and SQL receipts; advance only the expiry owner's clock."""
    store, version, actor = initialized(tmp_path)
    row, values = ready(version, actor)
    media = tmp_path / "media"
    media.mkdir(mode=0o700)
    create_bundle(media, row.pk, graphics())
    monkeypatch.setattr(
        cleanup, "database_now", lambda: timezone.now() + timedelta(days=2)
    )
    return store, version, actor, row, values, media


def produce():
    """Exercise the real metadata-only login and pinned singleton ownership."""
    with task_login(ServiceRole.SCHEDULER), scheduler_session() as guard:
        return cleanup.produce_cleanup(guard)


def arguments(media):
    """Compile a worker handler, never a broker-supplied callable or path."""
    return dict(
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={cleanup.TASK_TYPE: cleanup.cleanup_handler(media)},
    )


def test_expired_unselected_bundle_is_removed_once_by_actual_worker(staged):
    """Lost/duplicate queue hints and scheduler restarts cannot duplicate cleanup."""
    _, _, _, row, _, media = staged
    (task,) = produce()
    assert produce() == ()
    with task_login(ServiceRole.WORKER, reconnect=True):
        args = arguments(media)
        assert execute_hint(task.run_id, **args)
        assert not execute_hint(task.run_id, **args)
    row.refresh_from_db()
    assert row.state == "scrubbed" and row.assets.count() == 4
    assert not (media / "branding" / row.pk.hex).exists()
    assert TaskRun.objects.get(pk=task.run_id).state == "succeeded"
    assert produce() == ()


def test_current_and_historical_refs_excluded_before_bounded_page(staged):
    """Pinned early rows cannot starve a later eligible bundle even at limit one."""
    store, version, actor, row, values, media = staged
    assert (
        change(store, version, actor, branding_patch(version, values)).state
        == "applied"
    )
    second, _ = ready(store.active(), actor)
    with task_login(ServiceRole.SCHEDULER), scheduler_session() as guard:
        tasks = cleanup.produce_cleanup(guard, limit=1)
    assert [item.domain_request_id for item in tasks] == [second.pk]
    assert (media / "branding" / row.pk.hex).exists()


@pytest.mark.parametrize("selects_bundle", [False, True])
def test_pending_request_pins_only_its_selected_bundle(staged, selects_bundle):
    """Unrelated edits do not starve expired media; selecting a logo still pins it."""
    store, version, actor, row, values, media = staged
    patch = branding_patch(version, values)
    if not selects_bundle:
        patch[0]["values"] = {"name": "Pending public parish name"}
    status = record_request(
        base_digest=version.digest,
        patch=patch,
        actor_id=actor,
        request_key=uuid4(),
        correlation_id=uuid4(),
    )
    assert status.state == "staged" and store.active() == version
    if selects_bundle:
        assert produce() == ()
        with (
            pytest.raises(DatabaseError, match="Pending configuration"),
            transaction.atomic(),
        ):
            advance(row, "cleanup_pending")
        # Exclude the pinned earlier upload before applying the bounded limit.
        second, _ = ready(version, actor)
        with task_login(ServiceRole.SCHEDULER), scheduler_session() as guard:
            tasks = cleanup.produce_cleanup(guard, limit=1)
        assert [task.domain_request_id for task in tasks] == [second.pk]
    else:
        (task,) = produce()
        with task_login(ServiceRole.WORKER, reconnect=True):
            assert execute_hint(task.run_id, **arguments(media))
        row.refresh_from_db()
        assert row.state == "scrubbed"
        assert not (media / "branding" / row.pk.hex).exists()


def test_unexpired_uploads_restore_and_pending_configuration_are_holds(
    staged, monkeypatch
):
    """The producer does not consume a root while any authoritative hold applies."""
    _, _, _, row, _, media = staged
    with monkeypatch.context() as patch:
        patch.setattr(cleanup, "database_now", timezone.now)
        assert produce() == ()
    with restored_runtime(timezone.now()):
        assert produce() == ()
    # The installation guard's pending check has separate real request tests;
    # this also exercises a hold arriving after a task was already claimed.
    (task,) = produce()
    args = arguments(media)
    execution = claim_hint(task.run_id, **args)
    monkeypatch.setattr(cleanup, "available", lambda: False)
    with pytest.raises(PermissionError):
        execution.handler.execute(execution)
    assert (media / "branding" / row.pk.hex).exists()
    assert TaskRun.objects.get(pk=task.run_id).state == "running"


@pytest.mark.parametrize("held", [False, True])
def test_filesystem_failure_keeps_checkpoint_and_bounded_retry(
    staged, monkeypatch, held
):
    """No private exception text enters task rows; files remain until retry succeeds."""
    _, _, _, row, _, media = staged
    (task,) = produce()

    def fail(*args):
        """A synthetic filesystem error must not be treated as successful scrubbing."""
        if held:
            monkeypatch.setattr(cleanup, "available", lambda: False)
        raise ConfigError("private filesystem detail")

    monkeypatch.setattr(
        "parishkit.stewardship.accounts.branding_staging.remove_bundle", fail
    )
    with task_login(ServiceRole.WORKER, reconnect=True):
        execute_hint(task.run_id, **arguments(media))
    row.refresh_from_db()
    run = TaskRun.objects.get(pk=task.run_id)
    assert row.state == "cleanup_pending" and run.state == "retry_wait"
    assert "private" not in str(run.__dict__)
    assert (media / "branding" / row.pk.hex).exists()
    assert produce() == ()


@pytest.mark.parametrize("scrubbed", [False, True])
def test_expired_task_recovers_from_bundle_checkpoint(staged, scrubbed):
    """A vanished worker is not completion evidence; the durable scrub receipt is."""
    _, _, _, row, _, media = staged
    (task,) = produce()
    with work_transaction():
        change_run(
            run_id=task.run_id,
            expected_version=task.version,
            action="claim",
            actor_id=uuid4(),
            correlation_id=uuid4(),
            admit=cleanup.admit_cleanup,
            lease_seconds=1,
        )
    if scrubbed:
        from parishkit.stewardship.accounts.branding_staging import cleanup_branding

        cleanup_branding(media, row.pk, admit=lambda current: cleanup.eligible(current))
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_sleep(1.05)")
    with task_login(ServiceRole.WORKER):
        assert recover_hint(task.run_id, **arguments(media))
    assert TaskRun.objects.get(pk=task.run_id).state == (
        "succeeded" if scrubbed else "retry_wait"
    )


def test_cleanup_checks_current_task_view_and_actual_scrub_receipt(staged):
    """A made-up task type/version or a ready logo never authorizes completion."""
    *_, media = staged
    (task,) = produce()
    execution = claim_hint(task.run_id, **arguments(media))
    with work_transaction():
        status = _status(TaskRun.objects.get(pk=task.run_id))
        assert not cleanup.admit_cleanup("complete", status)
        for forged in (
            replace(status, version=status.version + 1),
            replace(status, task_type="other"),
        ):
            with pytest.raises(PermissionError):
                cleanup.admit_cleanup("effect", forged)
        with pytest.raises(PermissionError):
            cleanup.recover_cleanup(status)
    with pytest.raises(PermissionError):
        execution.transition("complete")


def test_terminal_cleanup_requires_explicit_retry_and_can_recover(staged):
    """A repaired mount can be retried without an unbounded automatic producer loop."""
    _, _, actor, row, _, media = staged
    (task,) = produce()
    execution = claim_hint(task.run_id, **arguments(media))
    execution.transition("permanent_failure")
    assert produce() == ()
    command = dict(
        run_id=task.run_id,
        command_id=uuid4(),
        actor_id=actor,
        correlation_id=uuid4(),
        admit=cleanup.admit_cleanup,
    )
    with task_login(ServiceRole.WORKER):
        with work_transaction():
            retried = retry_failed(**command)
            assert retry_failed(**command) == retried
        assert execute_hint(retried.run_id, **arguments(media))
        with work_transaction():
            assert retry_failed(**command).run_id == retried.run_id
    row.refresh_from_db()
    assert row.state == "scrubbed" and produce() == ()


def test_scheduler_cannot_mutate_media_receipts_or_run_cleanup(staged):
    """Only the general worker receives UPDATE and a mounted media dependency."""
    _, _, _, row, _, _ = staged
    with (
        task_login(ServiceRole.SCHEDULER),
        pytest.raises(DatabaseError),
        transaction.atomic(),
    ):
        advance(row, "cleanup_pending")
    with pytest.raises(PermissionError):
        cleanup.cleanup_handler().execute(None)


@pytest.mark.parametrize("limit", [0, 101, True, "20"])
def test_cleanup_producer_requires_bounded_owned_inputs(staged, limit):
    """A supplied count does not bypass the dedicated scheduler connection."""
    with scheduler_session() as guard, pytest.raises(TypeError):
        cleanup.produce_cleanup(guard, limit=limit)


def test_cleanup_producer_rejects_arbitrary_guard_and_outer_transaction():
    """No long surrounding transaction may retain metadata locks across removal."""
    with pytest.raises(TypeError):
        cleanup.produce_cleanup(object())
    with (
        scheduler_session() as guard,
        transaction.atomic(),
        pytest.raises(StorageInvariantError),
    ):
        cleanup.produce_cleanup(guard)


def test_cleanup_handler_rejects_relative_or_untyped_media_paths():
    """The worker path is deployment authority, never browser or queue input."""
    from pathlib import Path

    for path in ("/tmp/media", Path("media")):
        with pytest.raises(ValueError):
            cleanup.cleanup_handler(path)
