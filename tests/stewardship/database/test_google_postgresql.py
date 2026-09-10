"""Google cryptographic claims, durable sessions, early limits and namespace tests."""

from datetime import timedelta

import pytest
from django.contrib.sessions.models import Session
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.accounts.sessions import cleanup_admin_sessions
from parishkit.stewardship.audit.models import AuditEvent

from .auth_builders import signed_in, start

pytestmark = pytest.mark.django_db(transaction=True)


def test_full_google_flow_uses_pkce_nonce_signed_claims_and_no_password_user(
    auth_service, google
):
    """Authentication exchanges state for a new DB session without provider tokens."""
    from allauth.socialaccount.models import SocialAccount, SocialToken
    from django.contrib.auth.models import User

    client = Client(enforce_csrf_checks=True)
    query = start(client)
    before = client.cookies["pk_admin"].value
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["nonce"][0]) >= 32
    assert query["scope"] == ["openid email"]
    assert query["max_age"] == ["0"]
    response = client.get(
        "/admin/oauth/callback", {"code": "synthetic", "state": query["state"][0]}
    )
    assert response.status_code == 302
    assert response["Location"] == "/admin/"
    assert google[1][0] and len(google[1][0]) >= 43
    assert client.cookies["pk_admin"].value != before
    assert client.cookies["pk_admin"]["path"] == "/admin/"
    assert client.cookies["pk_admin"]["httponly"]
    assert "pk_family" not in client.cookies
    assert not User.objects.exists()
    assert not SocialAccount.objects.exists()
    assert not SocialToken.objects.exists()
    row = PortalSession.objects.get()
    assert row.expires_at - row.authenticated_at == timedelta(hours=12)
    assert PortalUser.objects.get().google_subject == "synthetic-google-subject"
    assert client.get("/admin/").status_code == 200
    data = Session.objects.get(pk=row.session_id).get_decoded()
    assert set(data) == {"principal", "recovery_epoch", "_session_expiry"}
    assert AuditEvent.objects.filter(event_type="admin_login").count() == 1


@pytest.mark.parametrize(
    "claims",
    [
        {"email_verified": False},
        {"nonce": "wrong"},
        {"nonce": "é"},
        {"aud": "different"},
        {"iss": "https://attacker.example"},
        {"exp": 1},
        {"sub": ""},
        {"email": "broken"},
    ],
)
def test_signed_but_invalid_claims_do_not_issue_session(auth_service, google, claims):
    """Even a trusted signature cannot compensate for wrong OIDC claim values."""
    google[0].update(claims)
    client, response = signed_in()
    assert response.status_code == 403
    assert not PortalSession.objects.exists()
    assert b"synthetic" not in response.content
    assert "/admin/login" in response.content.decode()


def test_signed_denied_account_is_not_authorized(auth_service, google):
    """A verified personal identity has no implicit parish role."""
    google[0]["email"] = "outsider@example.net"
    _, response = signed_in()
    assert response.status_code == 403
    assert not PortalSession.objects.exists()


def test_google_state_is_one_use_and_unknown_state_never_calls_provider(
    auth_service, google
):
    """Retrying a callback cannot replay a consumed provider state."""
    client = Client(enforce_csrf_checks=True)
    query = start(client)
    data = {"code": "synthetic", "state": query["state"][0]}
    assert client.get("/admin/oauth/callback", data).status_code == 302
    assert client.get("/admin/oauth/callback", data).status_code == 403
    assert len(google[1]) == 1


def test_csrf_logout_clears_only_admin_and_ordered_cleanup_works(auth_service, google):
    """Logout revokes immediately; expiry cleanup cannot be blocked by PROTECT."""
    client, _ = signed_in()
    row = PortalSession.objects.get()
    client.cookies["pk_family"] = "independent-family-cookie"
    assert client.post("/admin/logout").status_code == 403
    assert client.get("/admin/logout").status_code == 405
    response = client.post(
        "/admin/logout", {"csrfmiddlewaretoken": client.cookies["csrftoken"].value}
    )
    assert response.status_code == 302
    row.refresh_from_db()
    assert row.revoked_at is not None
    assert client.cookies["pk_family"].value == "independent-family-cookie"
    assert response.cookies["pk_admin"]["max-age"] == 0
    assert cleanup_admin_sessions() == 1
    assert not PortalSession.objects.exists()
    assert not Session.objects.filter(pk=row.session_id).exists()
    assert (
        AuditEvent.objects.filter(event_type="admin_logout", subject_id=row.pk).count()
        == 1
    )


def test_deactivation_revokes_existing_session_next_request(auth_service, google):
    """Policy is loaded from current identity metadata, not roles in a cookie."""
    client, _ = signed_in()
    PortalUser.objects.update(disabled=True, version=F("version") + 1)
    response = client.get("/admin/")
    assert response.status_code == 302
    assert PortalSession.objects.get().revoked_at is not None


def test_missing_state_and_preverification_limit_are_accounted_once(
    auth_service, google
):
    """Rejected attempts still reach aggregate detection without contacting Google."""
    client = Client()
    for _ in range(12):
        response = client.get("/admin/oauth/callback", {"code": "sensitive"})
    assert response.status_code == 429
    assert 1 <= int(response["Retry-After"]) <= 3600
    assert not google[1]
    key = auth_service.limiter.namespace + ":aggregate:admin:attempts"
    assert auth_service.limiter.client.zcard(key) == 12


@pytest.mark.parametrize(
    "path",
    [
        "/accounts/login/",
        "/accounts/signup/",
        "/admin/password/",
        "/admin/recovery/",
        "/accounts/google/login/token/",
    ],
)
def test_password_signup_recovery_and_direct_token_routes_absent(auth_service, path):
    assert Client().get(path).status_code == 404
