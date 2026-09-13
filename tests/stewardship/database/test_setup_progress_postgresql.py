"""Real source-lease evidence and original-login idle renewal with a hard watchdog."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F
from psycopg import sql

from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_progress import source_progress
from parishkit.stewardship.accounts.setup_staging import begin_setup, cancel_setup
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.source.leases import acquire_source, release_source
from parishkit.stewardship.source.models import SourceMutationLease

from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401
from .test_taskrun_postgresql import act, new

pytestmark = pytest.mark.django_db(transaction=True)


def loading(service, *, claimed=True, source=True):
    """Bind one synthetic provider task to real setup and lease storage owners."""
    request = login(service)
    attempt = begin_setup(request, service)
    task, claim = bind_load(attempt.attempt_id, claimed=claimed, source=source)
    return request, task, claim


def bind_load(attempt_id, *, claimed=True, source=True):
    """Provide synthetic work for service and HTTP tests without changing sessions."""
    SourceMutationLease.objects.get_or_create(singleton=True)
    row = SetupAttempt.objects.get(pk=attempt_id)
    task = new(
        task_type="setup_source_load", actor_id=row.owner_id, domain_request_id=row.pk
    )
    with work_transaction():
        SetupAttempt.objects.filter(pk=row.pk).update(
            source_task_id=task.run_id, state="loading", version=F("version") + 1
        )
    claim = None
    if claimed:
        task = act(task, "claim", lease_seconds=300)
        if source:
            run = TaskRun.objects.get(pk=task.run_id)
            claim = acquire_source(
                task_id=run.pk,
                task_fence=run.fence,
                worker_id=run.worker_id,
                phase="full",
                lease_seconds=300,
            )
    return task, claim


def aged_original_load(task_id, *, minutes):
    """Model a restored historical creation clock, never patch production time rules.

    This disposable schema-owner fixture changes only original creation instants.
    Runtime renewal is then tested with all triggers enabled, the real clock and
    restricted web grants; no five-minute sleep or disabled runtime check is used.
    """
    attempt_id = SetupAttempt.objects.get(source_task_id=task_id).pk
    with transaction.atomic(), connection.cursor() as cursor:
        for table, identifier in (
            ("stewardship_setup_attempt", attempt_id),
            ("stewardship_task_run", task_id),
        ):
            name = sql.Identifier(table)
            cursor.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER USER").format(name))
            try:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET created_at=created_at-%s WHERE id=%s"
                    ).format(name),
                    [timedelta(minutes=minutes), identifier],
                )
            finally:
                cursor.execute(
                    sql.SQL("ALTER TABLE {} ENABLE TRIGGER USER").format(name)
                )


def test_real_web_renewal_requires_five_minutes_and_preserves_hard_deadlines(
    setup_service,
):
    """A live worker does not itself renew a login or move its absolute lifetime."""
    request, task, _ = loading(setup_service)
    session = PortalSession.objects.get()
    with web_login():
        early = source_progress(request, setup_service, task.run_id, renew=True)
        assert not early["renewed"] and early["worker_live"]
        assert PortalSession.objects.get().last_activity_at == session.last_activity_at
    aged_original_load(task.run_id, minutes=6)
    with web_login():
        passive = source_progress(request, setup_service, task.run_id)
        assert not passive["renewed"]
        renewed = source_progress(request, setup_service, task.run_id, renew=True)
        assert renewed["renewed"] and renewed["active"]
        assert renewed["watchdog_at"] == passive["watchdog_at"]
        assert renewed["absolute_at"] == passive["absolute_at"]
        again = source_progress(request, setup_service, task.run_id, renew=True)
        assert not again["renewed"] and again["idle_at"] == renewed["idle_at"]
    row = SetupAttempt.objects.get()
    session.refresh_from_db()
    assert row.renewed_at <= session.last_activity_at
    assert session.expires_at.isoformat() == passive["absolute_at"]


@pytest.mark.parametrize("mode", ["queued", "no_source", "failed", "released"])
def test_unhealthy_or_unowned_worker_never_renews(setup_service, mode):
    """A Task ID, stale progress row or terminal success flag is not live work proof."""
    request, task, claim = loading(
        setup_service,
        claimed=mode != "queued",
        source=mode not in {"queued", "no_source"},
    )
    aged_original_load(task.run_id, minutes=6)
    if mode in {"released", "failed"}:
        release_source(claim)
    if mode == "failed":
        act(task, "permanent_failure")
    before = PortalSession.objects.get().last_activity_at
    with web_login():
        result = source_progress(request, setup_service, task.run_id, renew=True)
    assert not result["renewed"] and not result["worker_live"]
    assert PortalSession.objects.get().last_activity_at == before


def test_renewal_cannot_switch_to_a_different_login(setup_service, monkeypatch):
    """A session rotation during final recheck rolls back the attempted renewal."""
    from copy import copy

    from parishkit.stewardship.accounts import setup_progress

    request, task, _ = loading(setup_service)
    aged_original_load(task.run_id, minutes=6)
    session = PortalSession.objects.get()
    authenticate = setup_progress.authenticated_admin

    def rotate(*args, **kwargs):
        """Model the final authenticator returning a distinct session binding."""
        actor = authenticate(*args, **kwargs)
        request.portal_session = copy(request.portal_session)
        request.portal_session.pk = uuid4()
        return actor

    monkeypatch.setattr(setup_progress, "authenticated_admin", rotate)
    with web_login(), pytest.raises(PermissionError, match="expired during renewal"):
        source_progress(request, setup_service, task.run_id, renew=True)
    assert SetupAttempt.objects.get().renewed_at is None
    assert PortalSession.objects.get().last_activity_at == session.last_activity_at


def test_watchdog_expiry_is_nonextendable_and_commits_the_attempt_fence(setup_service):
    """A healthy worker cannot override the two-hour original Task deadline."""
    request, task, _ = loading(setup_service)
    aged_original_load(task.run_id, minutes=121)
    with web_login():
        passive = source_progress(request, setup_service, task.run_id)
        assert not passive["active"]
        assert SetupAttempt.objects.get().state == "loading"
        expired = source_progress(request, setup_service, task.run_id, renew=True)
        assert expired["setup_state"] == "expired" and not expired["renewed"]
        assert not expired["active"]
    assert SetupAttempt.objects.get().expiry_reason == "watchdog"


def test_another_login_or_task_cannot_use_the_exception(setup_service):
    """Correlation is bound to both original session and original task."""
    request, task, _ = loading(setup_service)
    newcomer = login(setup_service)
    for browser_request, identifier in ((newcomer, task.run_id), (request, uuid4())):
        with pytest.raises(LookupError):
            source_progress(browser_request, setup_service, identifier, renew=True)
    with pytest.raises(ValueError):
        source_progress(request, setup_service, str(task.run_id), renew=True)
    cancel_setup(request, setup_service, SetupAttempt.objects.get().pk)
    assert not source_progress(request, setup_service, task.run_id, renew=True)[
        "active"
    ]


def test_web_can_observe_but_not_change_source_lease_metadata():
    """Progress grants do not turn a browser into a provider mutation owner."""
    with (
        web_login(),
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("UPDATE stewardship_source_lease SET expires_at=now()")
