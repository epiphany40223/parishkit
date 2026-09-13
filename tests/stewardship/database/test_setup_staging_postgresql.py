"""Real bootstrap/session ownership and SQL barriers for initial setup metadata."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.db import IntegrityError, connection, transaction
from django.db.models import F
from django.test import RequestFactory

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.accounts.sessions import database_now, end_admin, issue_admin
from parishkit.stewardship.accounts.setup_models import SetupAttempt
from parishkit.stewardship.accounts.setup_policy import SetupState
from parishkit.stewardship.accounts.setup_staging import (
    begin_setup,
    cancel_setup,
    expire_setup_attempts,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError

from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_taskrun_postgresql import act, new

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def setup_service(bootstrapped):  # noqa: F811
    """A real minimal applied bootstrap contains no Parish or campaign configuration."""
    store, _, _, _ = bootstrapped
    return SimpleNamespace(store=store, configured=lambda: False)


def login(service):
    """Issue durable authority after a synthetic verified Google boundary."""
    user, _ = PortalUser.objects.get_or_create(
        google_subject="synthetic-setup-admin",
        defaults={"email": "admin@example.org", "verified_at": database_now()},
    )
    request = RequestFactory().post("/admin/setup")
    request.session = SessionStore()
    issue_admin(request, user.pk, store=service.store, authenticated_at=database_now())
    return request


def test_same_login_resumes_but_cancelled_attempt_never_restarts(setup_service):
    """Cancellation is an idempotent durable fence, not a request to clear history."""
    request = login(setup_service)
    first = begin_setup(request, setup_service)
    assert begin_setup(request, setup_service) == first
    expired = cancel_setup(request, setup_service, first.attempt_id)
    assert expired.state is SetupState.EXPIRED
    assert cancel_setup(request, setup_service, first.attempt_id) == expired
    assert begin_setup(request, setup_service) == expired
    assert SetupAttempt.objects.count() == 1
    assert SetupAttempt.objects.get().expiry_reason == "cancelled"
    assert AuditEvent.objects.filter(event_type=Action.SETUP_STARTED).count() == 1
    assert AuditEvent.objects.filter(event_type=Action.SETUP_EXPIRED).count() == 1


def test_another_login_cannot_take_over_or_cancel_live_staging(setup_service):
    """Even another session for the same Admin account cannot resume this wizard."""
    first, second = login(setup_service), login(setup_service)
    attempt = begin_setup(first, setup_service)
    with pytest.raises(StaleRecordError):
        begin_setup(second, setup_service)
    with pytest.raises(LookupError):
        cancel_setup(second, setup_service, attempt.attempt_id)
    assert SetupAttempt.objects.get().state == "collecting"


def test_logout_fences_old_attempt_and_a_new_login_can_start_another(setup_service):
    """Safe tombstones do not retain session foreign keys or migrate staged values."""
    request = login(setup_service)
    old = begin_setup(request, setup_service)
    end_admin(request)
    assert expire_setup_attempts() == 1
    assert expire_setup_attempts() == 0
    replacement = begin_setup(login(setup_service), setup_service)
    assert replacement.attempt_id != old.attempt_id
    assert SetupAttempt.objects.get(pk=old.attempt_id).expiry_reason == "session"


def test_begin_cleans_an_abandoned_attempt_before_claiming_new_ownership(setup_service):
    """Restart does not require a prior cleanup polling tick to observe revocation."""
    request = login(setup_service)
    old = begin_setup(request, setup_service)
    end_admin(request)
    replacement = begin_setup(login(setup_service), setup_service)
    assert replacement.attempt_id != old.attempt_id
    assert SetupAttempt.objects.get(pk=old.attempt_id).state == "expired"


def test_cleanup_detects_disabled_admin_without_waiting_for_idle_expiry(setup_service):
    """Authorization loss fences a worker before another browser request."""
    begin_setup(login(setup_service), setup_service)
    PortalUser.objects.update(disabled=True, version=F("version") + 1)
    assert expire_setup_attempts() == 1
    assert SetupAttempt.objects.get().expiry_reason == "session"


@pytest.mark.parametrize(
    "mutation",
    [
        {"state": "completed"},
        {"state": "frozen"},
        {"state": "loading"},
        {"session_id": uuid4()},
        {"owner_id": uuid4()},
        {"renewed_at": "now"},
    ],
)
def test_sql_cannot_skip_owners_rebind_or_fabricate_renewal(setup_service, mutation):
    """Knowledge of IDs and broad schema-owner test grants cannot bypass row guards."""
    attempt = begin_setup(login(setup_service), setup_service)
    if mutation.get("renewed_at") == "now":
        mutation = mutation | {"renewed_at": database_now()}
    with pytest.raises(IntegrityError), work_transaction():
        SetupAttempt.objects.filter(pk=attempt.attempt_id).update(
            **mutation, version=F("version") + 1
        )
    assert SetupAttempt.objects.get().state == "collecting"


def test_sql_requires_work_order_and_retains_tombstones(setup_service):
    """Deleting or changing a setup row outside its ordered owner fails closed."""
    request = login(setup_service)
    attempt = begin_setup(request, setup_service)
    with pytest.raises(IntegrityError), transaction.atomic():
        SetupAttempt.objects.filter(pk=attempt.attempt_id).update(
            version=F("version") + 1
        )
    cancel_setup(request, setup_service, attempt.attempt_id)
    with (
        pytest.raises(IntegrityError, match="Setup history cannot be deleted") as error,
        transaction.atomic(),
    ):
        SetupAttempt.objects.filter(pk=attempt.attempt_id).delete()
    assert error.value.__cause__.sqlstate == "23514"


def test_source_task_binding_is_exact_and_failed_work_cannot_be_frozen(setup_service):
    """Only the original source task may be correlated; unrelated jobs are rejected."""
    request = login(setup_service)
    attempt = begin_setup(request, setup_service)
    row = SetupAttempt.objects.get(pk=attempt.attempt_id)
    foreign = new(task_type="setup_source_load", actor_id=row.owner_id)
    with pytest.raises(IntegrityError), work_transaction():
        SetupAttempt.objects.filter(pk=row.pk).update(
            source_task_id=foreign.run_id, state="loading", version=F("version") + 1
        )
    task = new(
        task_type="setup_source_load", actor_id=row.owner_id, domain_request_id=row.pk
    )
    with work_transaction():
        SetupAttempt.objects.filter(pk=row.pk).update(
            source_task_id=task.run_id, state="loading", version=F("version") + 1
        )
    act(act(task, "claim"), "permanent_failure")
    with pytest.raises(IntegrityError), work_transaction():
        SetupAttempt.objects.filter(pk=row.pk).update(
            state="collecting", version=F("version") + 1
        )
    cancel_setup(request, setup_service, row.pk)
    with pytest.raises(IntegrityError), work_transaction():
        SetupAttempt.objects.filter(pk=row.pk).update(
            state="collecting",
            expiry_reason="",
            expired_at=None,
            version=F("version") + 1,
        )


def test_active_attempt_cleanup_is_nonmutating_and_anonymous_is_denied(setup_service):
    """Periodic sweeps do not renew activity or invalidate a genuinely live wizard."""
    request = login(setup_service)
    first = begin_setup(request, setup_service)
    activity = PortalSession.objects.get().last_activity_at
    assert expire_setup_attempts() == 0
    assert SetupAttempt.objects.get().version == first.version
    assert PortalSession.objects.get().last_activity_at == activity
    anonymous = RequestFactory().post("/admin/setup")
    anonymous.session = SessionStore()
    with pytest.raises(PermissionError):
        begin_setup(anonymous, setup_service)


@pytest.mark.parametrize("state", ["collecting", "loading", "frozen"])
def test_frozen_attempt_cannot_resume_or_change_attribution(setup_service, state):
    """The frozen-state predicate is a rejection, not a transition allowlist."""
    request = login(setup_service)
    attempt = begin_setup(request, setup_service)
    row = SetupAttempt.objects.get(pk=attempt.attempt_id)
    task = new(
        task_type="setup_source_load", actor_id=row.owner_id, domain_request_id=row.pk
    )
    with work_transaction():
        SetupAttempt.objects.filter(pk=row.pk).update(
            source_task_id=task.run_id, state="loading", version=F("version") + 1
        )
    act(act(task, "claim"), "complete")
    with work_transaction():
        for target in ("collecting", "frozen"):
            SetupAttempt.objects.filter(pk=row.pk).update(
                state=target, version=F("version") + 1
            )
    before = AuditEvent.objects.count()
    for actor in (row.owner_id, uuid4(), None):
        with pytest.raises(IntegrityError, match="owning work"), work_transaction():
            SetupAttempt.objects.filter(pk=row.pk).update(
                state=state, actor_id=actor, version=F("version") + 1
            )
    assert AuditEvent.objects.count() == before
    assert SetupAttempt.objects.get(pk=row.pk).state == "frozen"


def test_installed_setup_deadlines_and_audit_vocabulary_match_python():
    """Changing a Python deadline requires the corresponding explicit SQL migration."""
    from parishkit.stewardship.accounts import setup_policy
    from parishkit.stewardship.accounts.sessions import ADMIN_IDLE

    assert setup_policy.IDLE_LIMIT == ADMIN_IDLE
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef("
            "'stewardship_setup_attempt_guard_v1()'::regprocedure)"
        )
        guard = cursor.fetchone()[0]
        for value, unit in (
            (setup_policy.IDLE_LIMIT, "minutes"),
            (setup_policy.RENEWAL_INTERVAL, "minutes"),
            (setup_policy.SOURCE_WATCHDOG, "hours"),
        ):
            count = int(value.total_seconds() / (60 if unit == "minutes" else 3600))
            assert f"interval '{count} {unit}'" in guard
        cursor.execute(
            "SELECT pg_get_functiondef("
            "'stewardship_setup_attempt_audit_v1()'::regprocedure)"
        )
        audit = cursor.fetchone()[0]
        for event in (
            Action.SETUP_STARTED,
            Action.SETUP_SOURCE_STARTED,
            Action.SETUP_SOURCE_COMPLETED,
            Action.SETUP_FROZEN,
            Action.SETUP_EXPIRED,
        ):
            assert f"'{event.value}'" in audit


def test_configured_runtime_and_non_uuid_cancellation_are_not_admitted(setup_service):
    """A restored configured deployment never starts a new first-time wizard."""
    request = login(setup_service)
    setup_service.configured = lambda: True
    with pytest.raises(ConfigError):
        begin_setup(request, setup_service)
    assert not SetupAttempt.objects.exists()
    with pytest.raises(TypeError):
        cancel_setup(request, setup_service, "not-an-attempt")
