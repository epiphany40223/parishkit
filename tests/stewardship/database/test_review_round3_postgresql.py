"""Third-review admission, audit and retry checks using real durable records."""

# Imported pytest fixtures are intentionally also named by test arguments.
# ruff: noqa: F811

from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.models import SocialApp
from django.core.exceptions import MultipleObjectsReturned
from django.db import transaction
from django.test import Client

from parishkit.stewardship.accounts.auth_incidents import record_incident
from parishkit.stewardship.accounts.auth_models import AuthenticationIncident
from parishkit.stewardship.accounts.credential_installation import (
    CredentialValidationUnavailable,
)
from parishkit.stewardship.accounts.cryptography import CodeMacKeyring, Key
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.schemas import Action, ActorKind
from parishkit.stewardship.audit.services import record_action
from parishkit.stewardship.campaigns.credential_keys import add_rotation_key
from parishkit.stewardship.campaigns.credential_models import FamilyCodeFingerprint
from parishkit.stewardship.campaigns.mac_rotation import (
    backfill_mac_batch,
    collision_only,
)

from .auth_builders import start
from .credential_builders import family_campaign
from .test_credential_isolation_postgresql import (  # noqa: F401
    acknowledge,
    installer,
    isolated_roles,
    run,
    stage_for,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_campaign_audit_requires_owner_before_sql():
    """A programming error does not poison its caller's transaction."""
    with transaction.atomic():
        with pytest.raises(ValueError, match="parish ownership"):
            record_action(
                Action.INVALID_LINK, actor_kind=ActorKind.SYSTEM, campaign_id=uuid4()
            )
        assert AuditEvent.objects.count() == 0


@pytest.mark.parametrize("decision", [None, False, 1])
def test_mac_mutations_require_explicit_admission(tmp_path, decision):
    """Both migration phases fail before decrypting or writing retained codes."""
    _, campaign, _, ring = family_campaign(tmp_path)
    rotating = CodeMacKeyring(
        [Key("m1", "lookup-only", b"m" * 32), Key("m2", "active", b"n" * 32)]
    )
    demoted = CodeMacKeyring([Key("m1", "collision-only", b"m" * 32), rotating.active])
    add_rotation_key(ring.mac, rotating)
    with pytest.raises(PermissionError, match="not admitted"):
        backfill_mac_batch(
            campaign_id=campaign.pk,
            general=ring.general,
            mac=rotating,
            admit=lambda: decision,
        )
    with pytest.raises(PermissionError, match="not admitted"):
        collision_only(rotating, demoted, admit=lambda: decision)
    assert not FamilyCodeFingerprint.objects.filter(key_id="m2").exists()


def test_retryable_provider_validation_keeps_sealed_request(installer):
    """Transient external failure neither burns the request nor installs a file."""
    identifier = stage_for(installer)
    installer.validate = Mock(side_effect=CredentialValidationUnavailable())
    with pytest.raises(CredentialValidationUnavailable):
        run(installer)
    row = SecretReplacementRequest.objects.get(pk=identifier)
    assert row.state == "testing"
    installer.validate = lambda _: True
    assert run(installer).state == "awaiting_ack"
    acknowledge(identifier)
    assert run(installer).state == "applied"


@pytest.mark.parametrize("error", [SocialApp.DoesNotExist, MultipleObjectsReturned])
@pytest.mark.parametrize("callback", [False, True])
def test_missing_or_ambiguous_google_app_is_retryable(
    auth_service, monkeypatch, error, callback
):
    """Both entry points use the same safe runtime-unavailable response."""
    monkeypatch.setattr(
        DefaultSocialAccountAdapter,
        "get_provider",
        Mock(side_effect=error("synthetic private config")),
    )
    browser = Client()
    response = (
        browser.get("/admin/oauth/callback")
        if callback
        else browser.post("/admin/login")
    )
    assert response.status_code == 503 and response["Retry-After"] == "5"
    assert b"synthetic" not in response.content


def test_cancelled_google_roundtrip_is_not_an_authentication_failure(auth_service):
    """A state-validated cancellation uses the coarse bucket, not failure counters."""
    browser = Client(enforce_csrf_checks=True)
    state = start(browser)["state"][0]
    response = browser.get(
        "/admin/oauth/callback", {"error": "access_denied", "state": state}
    )
    assert response.status_code == 403
    assert not AuditEvent.objects.filter(event_type="admin_login_denied").exists()
    assert not list(
        auth_service.limiter.client.scan_iter(
            auth_service.limiter.namespace + ":aggregate:*"
        )
    )


def test_incident_resolution_uses_database_clock(monkeypatch):
    """A future application clock cannot assign durable outage instants."""
    from django.utils import timezone

    before = database_now()
    monkeypatch.setattr(timezone, "now", lambda: before + timedelta(days=10))
    record_incident("limiter_unavailable", 2, 0, (0, 0, 0, 0))
    record_incident("limiter_available", 0, 0, (0, 0, 0, 0))
    row = AuthenticationIncident.objects.get()
    assert before <= row.resolved_at <= database_now()
    assert (
        before.timestamp() * 1_000_000
        <= row.window
        <= database_now().timestamp() * 1_000_000
    )


def test_nested_access_admission_cannot_downgrade_to_local_bucket(
    auth_service, monkeypatch
):
    """Caller misuse is not a store outage and cannot multiply the flood allowance."""
    monkeypatch.setattr(
        auth_service.limiter.fallback,
        "consume",
        lambda *args, **kwargs: pytest.fail("fallback"),
    )
    with transaction.atomic(), pytest.raises(LimiterUnavailable, match="independent"):
        auth_service.limiter.bucket("access", "192.0.2.1")
