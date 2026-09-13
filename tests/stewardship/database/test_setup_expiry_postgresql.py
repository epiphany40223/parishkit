"""The real metadata scheduler fences abandoned setup without session secrets."""

# ruff: noqa: F811 -- imported pytest fixtures are injected by name.

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.sessions import database_now, end_admin
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_staging import (
    begin_setup,
    produce_setup_expiry,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.jobs.scheduler import scheduler_session
from parishkit.stewardship.storage import StorageInvariantError

from .campaign_builders import restored_runtime
from .test_background_grants_postgresql import task_login
from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_setup_staging_postgresql import login, setup_service  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


def sweep():
    """Use the exact deployed SQL role and the real singleton scheduler lock."""
    with task_login(ServiceRole.SCHEDULER, exact=True), scheduler_session() as guard:
        return produce_setup_expiry(guard)


def test_scheduler_fences_revoked_setup_once_without_browser_activity(setup_service):
    """A live wizard remains unchanged; logout makes the next tick expire it."""
    request = login(setup_service)
    attempt = begin_setup(request, setup_service)
    assert sweep() == 0
    assert SetupAttempt.objects.get().version == attempt.version
    end_admin(request)
    assert sweep() == 1
    assert sweep() == 0
    row = SetupAttempt.objects.get()
    assert row.state == "expired" and row.expiry_reason == "session"
    assert row.actor_id is None
    assert AuditEvent.objects.filter(event_type="setup_expired").count() == 1


def test_restore_holds_even_already_revoked_setup(setup_service):
    """A restore does not authorize background destruction of staged artifacts."""
    request = login(setup_service)
    begin_setup(request, setup_service)
    end_admin(request)
    with restored_runtime(database_now()):
        assert sweep() == 0
    assert SetupAttempt.objects.get().state == "collecting"
    assert sweep() == 1


@pytest.mark.parametrize(
    "mutation",
    [
        {"state": "expired", "actor_id": None},
        {"state": "collecting", "actor_id": None},
        {"state": "expired", "actor_id": "owner"},
        {"renewed_at": None},
        {"source_task_id": None},
    ],
)
def test_scheduler_cannot_forge_live_expiry_or_advance_setup(setup_service, mutation):
    """Column grants and the SQL owner guard independently constrain the producer."""
    attempt = begin_setup(login(setup_service), setup_service)
    row = SetupAttempt.objects.get(pk=attempt.attempt_id)
    if mutation.get("actor_id") == "owner":
        mutation = mutation | {"actor_id": row.owner_id}
    with (
        task_login(ServiceRole.SCHEDULER, exact=True),
        pytest.raises(DatabaseError),
        work_transaction(),
    ):
        SetupAttempt.objects.filter(pk=row.pk).update(
            **mutation, version=F("version") + 1
        )
    row.refresh_from_db()
    assert row.state == "collecting" and row.version == attempt.version


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM stewardship_portal_session",
        "SELECT google_subject FROM stewardship_portal_user",
        "SELECT * FROM django_session",
        "UPDATE stewardship_portal_session SET revoked_at=now()",
    ],
)
def test_scheduler_metadata_reads_do_not_expose_login_secrets(statement):
    """Expiry does not grant reusable session or Google subject access."""
    with (
        task_login(ServiceRole.SCHEDULER, exact=True),
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)


def test_expiry_producer_requires_actual_ownership_and_short_transaction():
    """Neither a duck-typed permit nor a long enclosing transaction is accepted."""
    with pytest.raises(TypeError):
        produce_setup_expiry(object())
    with (
        scheduler_session() as guard,
        transaction.atomic(),
        pytest.raises(StorageInvariantError),
    ):
        produce_setup_expiry(guard)
