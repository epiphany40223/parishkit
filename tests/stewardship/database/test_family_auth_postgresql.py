"""Real Family HTTP exchange, disjoint cookies, current epochs and idle controls."""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts.family_authentication import FamilyRuntime
from parishkit.stewardship.campaigns.credential_models import (
    FamilyCampaign,
    FamilySession,
    RehearsalCredential,
)
from parishkit.stewardship.campaigns.family_identity import FamilyStatus
from parishkit.stewardship.campaigns.family_identity import (
    code_context as production_code_context,
)
from parishkit.stewardship.campaigns.lifecycle import CampaignWorkKind
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.rehearsals import (
    code_context,
    invalidate_rehearsal,
    prepare_rehearsals,
    token_context,
)

from .campaign_builders import add_draft, campaign_clock
from .credential_builders import keys, populate

pytestmark = pytest.mark.django_db(transaction=True)


@dataclass
class FamilyHarness:
    """No source/provider credentials: only a real campaign and synthetic keyring."""

    campaign: object
    rings: object
    service: FamilyRuntime
    code: str
    token: str


@pytest.fixture
def family_service(auth_service, settings):
    """Install the Family overlay through actual population and epoch services."""
    _, row, _ = add_draft(auth_service.store, auth_service.store.active(), uuid4())
    campaign = Campaign.objects.get(pk=UUID(row["id"]))
    ring = keys()
    populate(campaign, ring)
    service = FamilyRuntime(
        auth_service.store, auth_service.limiter, ring.general, ring.mac, ring.public
    )
    settings.STEWARDSHIP_FAMILY_RUNTIME = service
    with campaign_clock(campaign.active_configuration.starts_at):
        prepare_rehearsals(
            campaign_id=campaign.pk,
            family_ids=list(FamilyCampaign.objects.values_list("pk", flat=True)),
            general=ring.general,
            mac=ring.mac,
            public=ring.public,
            purpose=CampaignWorkKind.REHEARSAL,
            admit=lambda *args: None,
        )
        credential = RehearsalCredential.objects.get()
        yield FamilyHarness(
            campaign,
            ring,
            service,
            ring.general.decrypt(
                credential.code_ciphertext, context=code_context(credential.pk)
            ).decode(),
            ring.private.decrypt(
                credential.token_ciphertext, context=token_context(credential.pk)
            ).decode(),
        )


def login(code, client=None):
    """Exercise the rendered CSRF form and actual cookie namespace."""
    client = client or Client(enforce_csrf_checks=True)
    assert client.get("/").status_code == 200
    response = client.post(
        "/", {"code": code, "csrfmiddlewaretoken": client.cookies["csrftoken"].value}
    )
    return client, response


def test_ordered_family_cleanup_preserves_audit_attribution(family_service):
    from django.contrib.sessions.models import Session

    from parishkit.stewardship.accounts.sessions import cleanup_family_sessions
    from parishkit.stewardship.audit.models import AuditEvent

    client, response = login(family_service.code)
    assert response.status_code == 302
    row = FamilySession.objects.get()
    assert cleanup_family_sessions() == 0
    response = client.post(
        "/family/logout",
        {
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        },
    )
    assert response.status_code == 302
    assert cleanup_family_sessions() == 1
    assert not FamilySession.objects.filter(pk=row.pk).exists()
    assert not Session.objects.filter(pk=row.session_id).exists()
    assert AuditEvent.objects.filter(subject_id=row.pk).exists()


def test_invalid_link_audit_is_bounded_and_attempts_stay_ephemeral(family_service):
    """Different garbage links count individually but cannot grow durable audit."""
    from parishkit.stewardship.audit.models import AuditContext, AuditEvent

    for index in range(10):
        assert Client().get(f"/access/invalid-{index}").status_code == 403
    limiter = family_service.service.limiter
    assert limiter.client.zcard(limiter.namespace + ":aggregate:family:attempts") == 10
    event = AuditEvent.objects.get(event_type="family_link_invalid")
    assert AuditContext.objects.get(event=event).context == {"outcome": "denied"}


def test_code_exchange_and_token_link_use_clean_isolated_session(family_service):
    client, response = login(family_service.code.lower())
    assert response.status_code == 302 and response["Location"] == "/family/"
    assert "pk_admin" not in response.cookies
    assert response.cookies["pk_family"]["httponly"]
    assert response.cookies["pk_family"]["samesite"] == "Lax"
    assert client.get("/family/").status_code == 200
    first = client.cookies["pk_family"].value
    response = client.get("/access/" + family_service.token)
    assert response.status_code == 302 and response["Location"] == "/family/"
    assert client.cookies["pk_family"].value != first
    assert response["Referrer-Policy"] == "no-referrer"
    assert FamilySession.objects.filter(revoked_at__isnull=True).count() == 1
    row = FamilySession.objects.get(revoked_at__isnull=True)
    assert row.expires_at - row.authenticated_at == timedelta(hours=4)
    assert set(row.session.get_decoded()) == {"family", "_session_expiry"}


def test_production_code_never_authenticates_testing(family_service):
    family = FamilyCampaign.objects.get()
    code = family_service.rings.general.decrypt(
        family.code_ciphertext, context=production_code_context(family.pk)
    ).decode()
    _, response = login(code)
    assert response.status_code == 403
    assert not FamilySession.objects.exists()


def test_invalidation_ends_testing_sessions_before_sensitive_cleanup(family_service):
    client, _ = login(family_service.code)
    assert client.get("/family/").status_code == 200
    invalidate_rehearsal(
        campaign_id=family_service.campaign.pk, admit=lambda *args: None
    )
    assert RehearsalCredential.objects.exists()
    assert client.get("/family/").status_code == 302
    assert FamilySession.objects.get().revoked_at is not None
    assert client.get("/access/" + family_service.token).status_code == 403


def test_keepalive_is_empty_csrf_protected_rate_bounded_and_passive(
    family_service, monkeypatch
):
    from parishkit.stewardship.accounts import family_authentication

    client, _ = login(family_service.code)
    row = FamilySession.objects.get()
    original = row.last_activity_at
    instant = original + timedelta(minutes=10)
    monkeypatch.setattr(family_authentication, "database_now", lambda: instant)
    assert (
        client.post(
            "/family/keepalive", b"", content_type="application/json"
        ).status_code
        == 403
    )
    csrf = client.cookies["csrftoken"].value
    assert (
        client.post(
            "/family/keepalive",
            b"{}",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        ).status_code
        == 400
    )
    response = client.post(
        "/family/keepalive", b"", content_type="application/json", HTTP_X_CSRFTOKEN=csrf
    )
    assert response.status_code == 200
    row.refresh_from_db()
    assert row.last_activity_at == instant
    version = row.version
    assert (
        client.post(
            "/family/keepalive",
            b"",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf,
        ).status_code
        == 200
    )
    row.refresh_from_db()
    assert row.version == version
    assert row.expires_at == row.authenticated_at + timedelta(hours=4)


def test_idle_and_absolute_deadlines_cannot_be_extended(family_service, monkeypatch):
    from parishkit.stewardship.accounts import family_authentication

    client, _ = login(family_service.code)
    row = FamilySession.objects.get()
    monkeypatch.setattr(
        family_authentication,
        "database_now",
        lambda: row.last_activity_at + timedelta(hours=1),
    )
    assert client.get("/family/").status_code == 302
    row.refresh_from_db()
    assert row.revoked_at is not None


def test_pair_failures_do_not_lock_reactivated_valid_code(family_service):
    populate(
        family_service.campaign,
        family_service.rings,
        [FamilyStatus(1, False, False, False, False, "inactive", "ineligible")],
        generation=2,
    )
    client = Client(enforce_csrf_checks=True)
    for _ in range(6):
        _, response = login(family_service.code, client)
    assert response.status_code == 429
    populate(family_service.campaign, family_service.rings, generation=3)
    _, response = login(family_service.code, client)
    assert response.status_code == 302


def test_invalid_length_counts_only_ip_and_ascii_ilo_counts_pair(family_service):
    client = Client(enforce_csrf_checks=True)
    assert login("bad", client)[1].status_code == 403
    limiter = family_service.service.limiter
    assert not list(
        limiter.client.scan_iter(limiter.namespace + ":window:family_pair:*")
    )
    assert login("ILOOOOOO", client)[1].status_code == 403
    assert (
        len(list(limiter.client.scan_iter(limiter.namespace + ":window:family_pair:*")))
        == 1
    )


def test_logout_does_not_touch_admin_cookie(family_service):
    client, _ = login(family_service.code)
    client.cookies["pk_admin"] = "separate-admin-cookie"
    response = client.post(
        "/family/logout", {"csrfmiddlewaretoken": client.cookies["csrftoken"].value}
    )
    assert response.status_code == 302
    assert response.cookies["pk_family"]["max-age"] == 0
    assert "pk_admin" not in response.cookies
    assert FamilySession.objects.get().revoked_at is not None


def test_restored_deployment_epoch_invalidates_existing_family_session(family_service):
    from parishkit.stewardship.campaigns.credential_models import (
        DeploymentCredentialState,
    )

    client, _ = login(family_service.code)
    DeploymentCredentialState.objects.update(
        family_link_epoch=uuid4(), version=F("version") + 1
    )
    assert client.get("/family/").status_code == 302
