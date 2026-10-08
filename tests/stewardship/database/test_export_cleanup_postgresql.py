"""Actual expiry/crash cleanup keeps report history and calculation references."""

from uuid import uuid4

import pytest
from django.db import connection, transaction

from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.dispatch import execute_hint
from parishkit.stewardship.jobs.queues import WorkQueue
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.reports.export_cleanup import (
    TASK_TYPE,
    cleanup_handler,
    produce_cleanup,
)
from parishkit.stewardship.reports.export_models import (
    ExportArtifactCleanup,
    ExportAttempt,
    ExportPublication,
)
from parishkit.stewardship.reports.export_services import (
    ExportExpired,
    cancel_export,
    issue_download,
)
from parishkit.stewardship.reports.models import CampaignFactPin

from .test_background_grants_postgresql import task_login
from .test_export_jobs_postgresql import (  # noqa: F401
    request_export,
    run_export,
    scenario,
)
from .test_runtime_auth_grants_postgresql import web_login

pytestmark = pytest.mark.django_db(transaction=True)


def test_completed_unexpired_artifact_is_not_cleanup_work(scenario):  # noqa: F811
    """No time heuristic may thin a ready artifact before its recorded expiration."""
    request = request_export(scenario)
    run_export(scenario, request)
    with task_login(ServiceRole.SCHEDULER), scheduler_session() as guard:
        assert produce_cleanup(guard) == ()


def expire_publication(request):
    """Install only the aged fixture, then restore every application SQL guard."""
    publication = ExportPublication.objects.get(request=request)
    # Install an aged immutable fixture as schema owner. The application cleanup
    # then runs with every guard enabled; there is no production update port.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_export_publication DISABLE TRIGGER USER"
        )
        cursor.execute(
            "UPDATE stewardship_export_publication "
            "SET created_at=clock_timestamp()-interval '8 days', "
            "expires_at=clock_timestamp()-interval '1 day' WHERE id=%s",
            (publication.pk,),
        )
        cursor.execute("ALTER TABLE stewardship_export_publication ENABLE TRIGGER USER")
    return publication


def test_expired_download_is_reported_as_expiry(scenario):  # noqa: F811
    """The authorized requester learns the file expired, not that access ended."""
    store, principal, _, _ = scenario
    request = request_export(scenario)
    run_export(scenario, request)
    expire_publication(request)
    with pytest.raises(ExportExpired, match="expired"):
        issue_download(store, principal.pk, request.pk)


def test_expired_artifact_cleanup_keeps_requests_publication_and_pin(scenario):  # noqa: F811
    """A synthetic expired receipt exercises real restricted cleanup end to end."""
    _, _, _, root = scenario
    request = request_export(scenario)
    run_export(scenario, request)
    publication = expire_publication(request)
    target = root / "exports" / request.campaign_id.hex / publication.attempt_id.hex
    assert target.exists()
    with task_login(ServiceRole.SCHEDULER), scheduler_session() as guard:
        tasks = produce_cleanup(guard)
        assert len(tasks) == 1
        assert produce_cleanup(guard) == ()
    with task_login(ServiceRole.WORKER, reconnect=True):
        assert execute_hint(
            tasks[0].run_id,
            queue=WorkQueue.GENERAL,
            worker_id=uuid4(),
            handlers={TASK_TYPE: cleanup_handler(root)},
        )
    assert not target.exists()
    assert ExportArtifactCleanup.objects.get().attempt_id == publication.attempt_id
    assert ExportPublication.objects.get().pk == publication.pk
    assert CampaignFactPin.objects.filter(
        parent_kind="export", parent_id=request.pk
    ).exists()


def test_cleanup_times_out_without_unlinking_an_active_read(scenario):  # noqa: F811
    """An admitted download outlives expiry without being truncated by cleanup."""
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connections

    from parishkit.stewardship.campaigns.read_guards import CampaignReadGuard
    from parishkit.stewardship.jobs.models import TaskRun

    _, _, _, root = scenario
    request = request_export(scenario)
    run_export(scenario, request)
    publication = expire_publication(request)
    target = root / "exports" / request.campaign_id.hex / publication.attempt_id.hex
    with scheduler_session() as scheduler:
        task = produce_cleanup(scheduler)[0]

    def cleanup():
        """Run the real destructive owner on a separately connected worker."""
        try:
            return execute_hint(
                task.run_id,
                queue=WorkQueue.GENERAL,
                worker_id=uuid4(),
                handlers={TASK_TYPE: cleanup_handler(root)},
            )
        finally:
            connections.close_all()

    with (
        CampaignReadGuard(
            [request.campaign_id], authorize=lambda _: None, abort=lambda: None
        ),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        assert executor.submit(cleanup).result(timeout=15)
        assert target.exists()
    assert TaskRun.objects.get(pk=task.run_id).state == "retry_wait"
    assert not ExportArtifactCleanup.objects.exists()


def test_crashed_render_leaves_exact_attempt_for_cleanup(scenario, monkeypatch):  # noqa: F811
    """The attempt precedes output; terminal cancellation permits cleanup."""
    from parishkit.stewardship.jobs.dispatch import recover_hint
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status
    from parishkit.stewardship.reports import export_tasks

    from .test_taskrun_postgresql import act, expire

    store, principal, _, root = scenario
    request = request_export(scenario)
    original = export_tasks.write_artifact

    def crash(*args):
        """A complete file without a receipt remains unpublished work."""
        original(*args)
        raise RuntimeError("synthetic after-file crash")

    monkeypatch.setattr(export_tasks, "write_artifact", crash)
    with pytest.raises(RuntimeError):
        run_export(scenario, request)
    attempt = ExportAttempt.objects.get(request=request)
    assert (root / "exports" / request.campaign_id.hex / attempt.pk.hex).exists()
    cancel_export(store, principal.pk, request.pk)
    expire(
        act(
            _status(TaskRun.objects.get(pk=request.task_id)),
            "heartbeat",
            lease_seconds=1,
        )
    )
    assert recover_hint(
        request.task_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={
            export_tasks.TASK_TYPE: export_tasks.export_handler(store=store, root=root)
        },
    )
    with scheduler_session() as guard:
        tasks = produce_cleanup(guard)
    assert len(tasks) == 1
    assert execute_hint(
        tasks[0].run_id,
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: cleanup_handler(root)},
    )
    assert not (root / "exports" / request.campaign_id.hex / attempt.pk.hex).exists()


@pytest.mark.parametrize("crashed", [False, True])
def test_exhausted_cleanup_alerts_once_and_admin_can_retry(
    scenario,  # noqa: F811
    monkeypatch,
    crashed,
):
    """The fifth failure is visible and repairable without unbounded new roots."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.audit.models import OperationalLog
    from parishkit.stewardship.campaigns.work_locks import work_transaction
    from parishkit.stewardship.jobs.dispatch import recover_hint
    from parishkit.stewardship.jobs.models import TaskRun
    from parishkit.stewardship.jobs.storage import _status
    from parishkit.stewardship.reports import export_cleanup

    from .test_taskrun_postgresql import act, expire

    store, principal, _, root = scenario
    request = request_export(scenario)
    run_export(scenario, request)
    publication = expire_publication(request)
    with scheduler_session() as guard:
        task = produce_cleanup(guard)[0]
    # Reach the fifth claim through the actual journal, not an unguarded UPDATE.
    for attempt_number in range(4):
        with work_transaction():
            claimed = act(task, "claim")
            if attempt_number == 0:
                with pytest.raises(PermissionError):
                    act(
                        claimed, "permanent_failure", admit=export_cleanup.admit_cleanup
                    )
                assert not OperationalLog.objects.filter(
                    event="task_failed", level="CRITICAL"
                ).exists()
            task = act(claimed, "retryable_failure")
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(1.05)")
    options = dict(
        queue=WorkQueue.GENERAL,
        worker_id=uuid4(),
        handlers={TASK_TYPE: cleanup_handler(root)},
    )
    if crashed:
        with work_transaction():
            claimed = act(task, "claim", lease_seconds=1)
            for _ in range(2):
                assert export_cleanup.admit_cleanup("permanent_failure", claimed)
            assert not OperationalLog.objects.filter(
                event="task_failed", level="CRITICAL"
            ).exists()
            expire(claimed)
        with task_login(ServiceRole.WORKER):
            assert recover_hint(task.run_id, **options)
            assert not recover_hint(task.run_id, **options)
    else:

        def fail(*args):
            """Represent a filesystem fault handled by the owning worker."""
            raise ConfigError("synthetic removal failure")

        with monkeypatch.context() as patch:
            patch.setattr(export_cleanup, "remove_attempt_artifacts", fail)
            with task_login(ServiceRole.WORKER):
                assert execute_hint(task.run_id, **options)
                assert not execute_hint(task.run_id, **options)
    assert _status(TaskRun.objects.get(pk=task.run_id)).state == "failed"
    assert (
        OperationalLog.objects.filter(event="task_failed", level="CRITICAL").count()
        == 1
    )
    with scheduler_session() as guard:
        assert produce_cleanup(guard) == ()
    command = uuid4()
    with web_login():
        retried = export_cleanup.retry_cleanup(
            store, principal.pk, publication.attempt_id, request_key=command
        )
    assert (
        export_cleanup.retry_cleanup(
            store, principal.pk, publication.attempt_id, request_key=command
        ).run_id
        == retried.run_id
    )
    with task_login(ServiceRole.WORKER):
        assert execute_hint(retried.run_id, **options)
    assert ExportArtifactCleanup.objects.get().attempt_id == publication.attempt_id
    assert (
        OperationalLog.objects.filter(event="task_failed", level="CRITICAL").count()
        == 1
    )


def counted_work_locks(monkeypatch):
    """Count global work-order lock takes, keeping the real lock."""
    from parishkit.stewardship.campaigns import work_locks

    calls, real = [], work_locks.lock_work_order

    def counting():
        """Record one take, then take the real lock."""
        calls.append(1)
        return real()

    monkeypatch.setattr(work_locks, "lock_work_order", counting)
    return calls


def test_more_unowned_attempts_than_listed_go_to_the_locked_pass(
    scenario,  # noqa: F811
    monkeypatch,
):
    """Past UNOWNED_LIMIT the read before the lock decides nothing (#715).

    Three unowned attempts with a limit of two: the producer goes straight
    to the locked query, which still finds the newest, the only disposable
    one. The listing has no order, so the newest is normally the row a
    two-row listing leaves out; a read that judged only the listed rows
    would miss it.
    """
    from parishkit.stewardship.reports import export_cleanup

    monkeypatch.setattr(export_cleanup, "UNOWNED_LIMIT", 2)
    requests = [request_export(scenario) for _ in range(3)]
    for request in requests:
        run_export(scenario, request)
    newest = expire_publication(requests[-1])
    locks = counted_work_locks(monkeypatch)
    with task_login(ServiceRole.SCHEDULER), scheduler_session() as guard:
        (task,) = produce_cleanup(guard)
    assert task.domain_request_id == newest.attempt_id
    assert locks == [1]


def test_listed_attempts_none_disposable_take_no_lock(
    scenario,  # noqa: F811
    monkeypatch,
):
    """Within UNOWNED_LIMIT, no disposable attempt means no work-order lock."""
    from parishkit.stewardship.reports import export_cleanup

    monkeypatch.setattr(export_cleanup, "UNOWNED_LIMIT", 2)
    for _ in range(2):
        run_export(scenario, request_export(scenario))
    locks = counted_work_locks(monkeypatch)
    with task_login(ServiceRole.SCHEDULER), scheduler_session() as guard:
        assert produce_cleanup(guard) == ()
    assert locks == []
