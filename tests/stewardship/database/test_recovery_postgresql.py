"""Offline recovery uses real installer checkpoints without enabling a CLI."""

from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.db import IntegrityError, connection
from django.utils import timezone

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.configuration_installation import install_request
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.operator_recovery import recover_admin
from parishkit.stewardship.accounts.policy_models import (
    AdminRevocation,
    PolicySecurityEvent,
)
from parishkit.stewardship.accounts.request_models import ConfigurationChangeRequest
from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
from parishkit.stewardship.audit.models import AuditEvent

from ..policy_factory import address
from .test_policy_postgresql import initialized

pytestmark = pytest.mark.django_db(transaction=True)


@contextmanager
def synthetic_offline():
    """No application services are started against this disposable test deployment."""
    yield


def arguments():
    """Bind one operation to a synthetic operator, deployment and confirmed target."""
    return dict(
        operation_id=uuid4(),
        operator_name="Test operator",
        reason="Lost administration access",
        deployment_id=SystemConfiguration.objects.get().pk,
        target_email="replacement@example.org",
        confirmed_email="replacement@example.org",
        correlation_id=uuid4(),
        offline_interlock=synthetic_offline,
    )


def session():
    """Make a still-valid synthetic administration session to test revocation."""
    store = SessionStore()
    store.save()
    now = timezone.now()
    return PortalSession.objects.create(
        session_id=store.session_key,
        principal_id=uuid4(),
        authenticated_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(hours=1),
    )


def test_additive_recovery_is_attributed_revoking_and_idempotent(tmp_path):
    """Only the explicit Admin addition changes; evidence commits before success."""
    seed = address("replacement@example.org", ("ministry_leader",), seeded=True)
    store, initial, _ = initialized(tmp_path, [address(), seed])
    portal = session()
    kwargs = arguments()
    result = recover_admin(store, **kwargs)
    assert result.state == "applied"
    request = ConfigurationChangeRequest.objects.get(pk=result.request_id)
    assert request.actor_id is None and request.authority == "operator_recovery"
    assert request.operator_name == kwargs["operator_name"]
    assert request.operator_reason == kwargs["reason"]
    changed = next(
        record["values"]
        for record in store.active().document()["sections"]["login_rules"]
        if record["id"] == seed["id"]
    )
    assert changed == seed["values"] | {
        "roles": ["administrator", "ministry_leader"],
        "grants": seed["values"]["grants"]
        | {"administrator": {"manual": str(kwargs["operation_id"])}},
    }
    portal.refresh_from_db()
    assert portal.revoked_at and portal.version == 2
    assert AdminRevocation.objects.filter(activation__request=request).count() == 1
    assert PolicySecurityEvent.objects.get(
        target=kwargs["target_email"]
    ).recipients == ["admin@example.org", "replacement@example.org"]
    assert (
        AuditEvent.objects.filter(
            event_type="operator_admin_recovered", subject_id=request.pk
        ).count()
        == 1
    )
    runtime = SystemConfiguration.objects.get()
    assert runtime.mode == "testing" and not runtime.restore_review_required
    assert recover_admin(store, **kwargs) == result
    portal.refresh_from_db()
    assert portal.version == 2
    with pytest.raises(ConfigError, match="offline"):
        install_request(store, request_id=request.pk, correlation_id=uuid4())


@pytest.mark.parametrize(
    "field,value",
    [
        ("operator_name", "Different operator"),
        ("reason", "Different reason"),
        ("deployment_id", uuid4()),
        ("confirmed_email", "other@example.org"),
    ],
)
def test_recovery_replay_rejects_changed_binding(tmp_path, field, value):
    """An operation key is not permission to replace the already confirmed intent."""
    store, _, _ = initialized(tmp_path)
    kwargs = arguments()
    result = recover_admin(store, **kwargs)
    with pytest.raises(ConfigError):
        recover_admin(store, **(kwargs | {field: value}))
    assert store.active().version_id == result.applied_version_id


def test_recovery_activation_failure_retries_same_candidate(tmp_path):
    """A failed revocation rolls back activation; the YAML checkpoint is resumable."""
    store, initial, _ = initialized(tmp_path)
    portal = session()
    kwargs = arguments()
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE stewardship_admin_revocation "
            "ADD CONSTRAINT synthetic_recovery_failure CHECK (false) NOT VALID"
        )
    try:
        with pytest.raises(IntegrityError):
            recover_admin(store, **kwargs)
        assert (
            SystemConfiguration.objects.get().active_configuration_id
            == initial.version_id
        )
        portal.refresh_from_db()
        assert portal.revoked_at is None
        assert not PolicySecurityEvent.objects.filter(
            target=kwargs["target_email"]
        ).exists()
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE stewardship_admin_revocation "
                "DROP CONSTRAINT synthetic_recovery_failure"
            )
    prepared = store.active().version_id
    result = recover_admin(store, **kwargs)
    assert result.state == "applied" and result.applied_version_id == prepared
    assert (
        ConfigurationChangeRequest.objects.filter(authority="operator_recovery").count()
        == 1
    )


def test_recovery_must_acquire_interlock_even_for_replay(tmp_path):
    """No successful receipt bypasses the deployment's offline-startup interlock."""
    store, _, _ = initialized(tmp_path)
    kwargs = arguments()
    recover_admin(store, **kwargs)

    @contextmanager
    def unavailable():
        """Represent a running online service without stopping or bypassing it."""
        raise ConfigError("Online services are running.")
        yield

    with pytest.raises(ConfigError, match="running"):
        recover_admin(store, **(kwargs | {"offline_interlock": unavailable}))
