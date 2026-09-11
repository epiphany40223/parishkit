"""Offline recovery uses real installer checkpoints without enabling a CLI."""

from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.db import IntegrityError, connection, transaction
from django.db.models import F
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
from ..test_request_patch import parish_patch
from .campaign_builders import change, initialized

pytestmark = pytest.mark.django_db(transaction=True)


@contextmanager
def synthetic_offline():
    """No application services are started against this disposable test deployment."""
    yield


def arguments(deployment_id=None):
    """Bind one operation to a synthetic operator, deployment and confirmed target."""
    return dict(
        operation_id=uuid4(),
        operator_name="Test operator",
        reason="Lost administration access",
        deployment_id=SystemConfiguration.objects.get().pk
        if deployment_id is None
        else deployment_id,
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
    previews = []
    result = recover_admin(store, **kwargs, before_apply=previews.append)
    assert previews[0]["current_admin_rules"] == ["admin@example.org"]
    assert previews[0]["before_roles"] == ["ministry_leader"]
    assert previews[0]["after_roles"] == ["administrator", "ministry_leader"]
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
    assert recover_admin(store, **kwargs, before_apply=previews.append) == result
    assert previews[-1]["already_granted"] is True
    portal.refresh_from_db()
    assert portal.version == 2
    checkpoints = list(
        request.checkpoints.order_by("sequence").values_list("id", "state")
    )
    with pytest.raises(ConfigError, match="offline"):
        install_request(store, request_id=request.pk, correlation_id=uuid4())
    assert (
        list(request.checkpoints.order_by("sequence").values_list("id", "state"))
        == checkpoints
    )


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


def test_future_dated_activity_cannot_block_recovery(tmp_path):
    """Clock skew in session activity cannot defeat emergency revocation."""
    store, _, _ = initialized(tmp_path)
    portal = session()
    future = timezone.now() + timedelta(minutes=30)
    PortalSession.objects.filter(pk=portal.pk).update(
        last_activity_at=future, version=F("version") + 1
    )
    assert recover_admin(store, **arguments()).state == "applied"
    portal.refresh_from_db()
    assert portal.revoked_at >= future


def test_recovery_replay_rejects_new_confirmed_target(tmp_path):
    """Matching double entry cannot rebind an already persisted operation."""
    store, _, _ = initialized(tmp_path)
    kwargs = arguments()
    recover_admin(store, **kwargs)
    with pytest.raises(ConfigError, match="bound"):
        recover_admin(
            store,
            **(
                kwargs
                | {
                    "target_email": "other@example.org",
                    "confirmed_email": "other@example.org",
                }
            ),
        )


@pytest.mark.parametrize("field", ["operation_id", "deployment_id", "correlation_id"])
def test_recovery_rejects_non_uuid_identity(tmp_path, field):
    """Identity validation precedes offline acquisition and all durable writes."""
    with pytest.raises(TypeError):
        recover_admin(None, **(arguments(uuid4()) | {field: "invalid"}))


@pytest.mark.parametrize("field", ["operator_name", "reason"])
@pytest.mark.parametrize("value", ["", " bad ", "bad\nname", "x" * 1025])
def test_recovery_rejects_invalid_attribution(field, value):
    """Required bounded operator attribution cannot contain control characters."""
    with pytest.raises(ConfigError):
        recover_admin(None, **(arguments(uuid4()) | {field: value}))


def test_uninitialized_recovery_has_safe_diagnostic():
    """A wrong/uninitialized deployment is a configuration failure, not an ORM leak."""
    with pytest.raises(ConfigError, match="initialized"):
        recover_admin(None, **arguments(uuid4()))


def test_existing_admin_recovery_is_refused(tmp_path):
    """Recovery cannot masquerade as a new grant to an existing Administrator."""
    store, _, _ = initialized(tmp_path)
    with pytest.raises(ConfigError, match="already"):
        recover_admin(
            store,
            **(
                arguments()
                | {
                    "target_email": "admin@example.org",
                    "confirmed_email": "admin@example.org",
                }
            ),
        )


def test_mixed_case_confirmation_is_same_email(tmp_path):
    """Confirmation follows the same case-insensitive exact identity as login policy."""
    store, _, _ = initialized(tmp_path)
    assert (
        recover_admin(
            store,
            **(
                arguments()
                | {
                    "target_email": "Replacement@Example.org",
                    "confirmed_email": "Replacement@Example.org",
                }
            ),
        ).state
        == "applied"
    )


def test_failed_recovery_receipt_is_not_success_or_implicitly_retried(
    tmp_path, monkeypatch
):
    """A stale intent stays failed; a future command must require a new confirmation."""
    from parishkit.stewardship.accounts import operator_recovery

    store, initial, actor = initialized(tmp_path)
    kwargs = arguments()
    original = operator_recovery._install_request

    def supersede_then_install(store, **installation):
        """Simulate intervening operator work after a checkpoint/crash boundary."""
        change(store, initial, actor, parish_patch(initial, name="Updated Parish"))
        return original(store, **installation)

    with monkeypatch.context() as patcher:
        patcher.setattr(operator_recovery, "_install_request", supersede_then_install)
        result = recover_admin(store, **kwargs)
    assert result.state == "failed" and result.failure_code == "stale_base"
    assert recover_admin(store, **kwargs) == result
    assert not AdminRevocation.objects.exists()


def test_sql_recovery_creation_origin_must_match_operation(tmp_path):
    """Raw intent insertion cannot claim a different origin than the operator grant."""
    from parishkit.stewardship.accounts.request_patch import build_candidate

    store, initial, _ = initialized(tmp_path)
    kwargs = arguments()
    rule = address(kwargs["target_email"])
    rule["values"]["grants"]["administrator"]["manual"] = str(kwargs["operation_id"])
    intent = build_candidate(
        initial,
        [{"operation": "add", "section": "login_rules", **rule}],
        candidate_id=uuid4(),
    )
    with (
        pytest.raises(IntegrityError, match="recovery attribution"),
        transaction.atomic(),
    ):
        ConfigurationChangeRequest.objects.create(
            base_id=initial.version_id,
            patch=intent.patch(),
            actor_id=None,
            request_key=kwargs["operation_id"],
            request_schema="operator-recovery-patch-v1",
            payload_fingerprint=intent.payload_fingerprint,
            candidate_version_id=intent.candidate.version_id,
            candidate_digest=intent.candidate.digest,
            authority="operator_recovery",
            operator_name=kwargs["operator_name"],
            operator_reason=kwargs["reason"],
            confirmed_deployment_id=kwargs["deployment_id"],
            recovery_target=kwargs["target_email"],
        )
