"""Real disposable Valkey, signed synthetic Google tokens and durable policy."""

import os
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


def valkey_client():
    """Select an isolated test cluster without making host or credentials mutable."""
    port = int(os.environ.get("PARISHKIT_TEST_VALKEY_PORT", "56379"))
    if not 1024 <= port <= 65535 or port == 56380:
        raise ValueError("Use an unprivileged test port other than reserved 56380")
    return Redis(
        host="127.0.0.1", port=port, socket_timeout=2, socket_connect_timeout=2
    )


@pytest.fixture
def real_limiter():
    """Isolate real counters without unrelated configuration or Google setup."""
    client = valkey_client()
    assert client.ping()
    namespace = "parishkit-test:" + uuid4().hex
    limiter = Limiter(
        client,
        b"synthetic-test-limiter-key-material",
        incident=record_incident,
        namespace=namespace,
    )
    try:
        yield limiter
    finally:
        keys = list(client.scan_iter(namespace + ":*"))
        if keys:
            client.delete(*keys)
        client.close()


def auth_runtime(tmp_path, settings, limiter, records=None):
    """Assemble the ordinary authentication runtime over an initial policy.

    Tests that need Chairperson-seeded rules, which no ordinary patch can add,
    pass them as the initial `records`; everything else stays identical.
    """
    # The setup suites replace `initialized` with a one-argument stand-in, so
    # the optional records are passed only when a caller really supplies them.
    store, _, _ = (
        initialized(tmp_path) if records is None else initialized(tmp_path, records)
    )
    # These focused authentication tests explicitly model completed setup;
    # the setup integration suites exercise the real durable completion marker.
    settings.STEWARDSHIP_AUTH_RUNTIME = AuthRuntime(store, limiter, lambda: True)
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
    return settings.STEWARDSHIP_AUTH_RUNTIME


@pytest.fixture
def auth_service(tmp_path, settings, real_limiter):
    """Keep full configuration and HTTP login assembly for authentication tests."""
    return auth_runtime(tmp_path, settings, real_limiter)


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
            "auth_time": now,
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
