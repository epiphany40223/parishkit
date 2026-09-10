"""Real disposable Valkey, signed synthetic Google tokens and durable policy."""

from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import Client
from django.utils import timezone
from redis import Redis

from parishkit.stewardship.accounts.auth_incidents import record_incident
from parishkit.stewardship.accounts.authentication import (
    AuthRuntime,
    SignedGoogleAdapter,
)
from parishkit.stewardship.accounts.limiting import Limiter

from .campaign_builders import initialized


@pytest.fixture
def auth_service(tmp_path, settings):
    """Require the explicit loopback test services; missing Valkey fails, not skips."""
    store, _, _ = initialized(tmp_path)
    client = Redis(
        host="127.0.0.1", port=56379, socket_timeout=2, socket_connect_timeout=2
    )
    assert client.ping()
    namespace = "parishkit-test:" + uuid4().hex
    limiter = Limiter(
        client,
        b"synthetic-test-limiter-key-material",
        incident=record_incident,
        namespace=namespace,
    )
    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(store, limiter)
    settings.SOCIALACCOUNT_PROVIDERS = {
        "google": {
            "OAUTH_PKCE_ENABLED": True,
            "APPS": [
                {
                    "client_id": "synthetic-client",
                    "secret": "synthetic-secret",
                    "key": "",
                }
            ],
        },
    }
    try:
        yield settings.STEWARDSHIP_AUTH_RUNTIME
    finally:
        keys = list(client.scan_iter(namespace + ":*"))
        if keys:
            client.delete(*keys)
        client.close()


@pytest.fixture
def google(monkeypatch):
    """Keep JWT verification; replace only external certificate and exchange I/O."""
    from allauth.socialaccount.internal import jwtkit

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        jwtkit, "fetch_key", lambda *args: ("RS256", private.public_key())
    )
    claims = {
        "sub": "synthetic-google-subject",
        "email": "admin@example.org",
        "email_verified": True,
        "hd": "example.org",
    }
    seen = []

    def exchange(adapter, request, app, client, pkce_code_verifier=None):
        """Produce a short-lived correctly signed token for the exact issued nonce."""
        seen.append(pkce_code_verifier)
        now = int(timezone.now().timestamp())
        data = {
            "iss": "https://accounts.google.com",
            "aud": "synthetic-client",
            "iat": now,
            "exp": now + 60,
            "nonce": request.stewardship_oauth_state["data"]["nonce"],
            **claims,
        }
        return {
            "access_token": "synthetic-access-only",
            "id_token": jwt.encode(data, private, algorithm="RS256"),
        }

    monkeypatch.setattr(SignedGoogleAdapter, "get_access_token_data", exchange)
    return claims, seen


def start(client):
    """Use the browser's CSRF form, not a fabricated authenticated session."""
    assert client.get("/admin/login").status_code == 200
    response = client.post(
        "/admin/login", {"csrfmiddlewaretoken": client.cookies["csrftoken"].value}
    )
    assert response.status_code == 302
    return parse_qs(urlsplit(response["Location"]).query)


def signed_in():
    """Complete ordinary HTTP login against only the synthetic Google boundary."""
    client = Client(enforce_csrf_checks=True)
    query = start(client)
    response = client.get(
        "/admin/oauth/callback", {"code": "synthetic-code", "state": query["state"][0]}
    )
    return client, response
