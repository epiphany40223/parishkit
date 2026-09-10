"""Credential-free hostile ingress, security headers and private error tests."""

from types import SimpleNamespace

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from parishkit.stewardship.deployment import DeploymentProfile
from parishkit.stewardship.web.security import (
    SecurityBoundaryMiddleware,
    browser_settings,
    resolve_client,
)


def test_production_browser_profile_redirects_and_preserves_hsts_on_denials(client):
    """Startup consumes this primitive later; a 404 must not lose transport policy."""
    profile = browser_settings(
        SimpleNamespace(
            profile=DeploymentProfile.PRODUCTION,
            public_origin="https://testserver",
        )
    )
    with override_settings(**profile):
        assert client.get("/unknown").status_code == 301
        response = client.get("/unknown", secure=True)
        assert response.status_code == 404
        assert response["Strict-Transport-Security"] == "max-age=31536000"
        assert client.get("/health/live").status_code == 200
    assert profile["SESSION_COOKIE_SECURE"] and profile["CSRF_COOKIE_SECURE"]
    assert profile["SECURE_PROXY_SSL_HEADER"] is None


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.org",
        "https://user@example.org",
        "https://example.org/path",
        "https://example.org/?secret",
    ],
)
def test_production_origin_cannot_downgrade_or_contain_private_values(origin):
    with pytest.raises(ValueError):
        browser_settings(
            SimpleNamespace(profile=DeploymentProfile.PRODUCTION, public_origin=origin)
        )


@pytest.mark.parametrize(
    "url", ["/", "/admin/login", "/missing", "/access/private-token", "/health/live"]
)
def test_security_headers_cover_success_and_errors(client, url):
    """Every response receives the same restrictive browser envelope."""
    response = client.get(url)
    assert response["Cache-Control"] == "no-store"
    assert response["Referrer-Policy"] == "no-referrer"
    assert response["X-Frame-Options"] == "DENY"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'none'" in response["Content-Security-Policy"]
    assert "unsafe-inline" not in response["Content-Security-Policy"]


@pytest.mark.parametrize("debug", [False, True])
def test_unknown_secret_path_is_not_reflected_even_with_debug(client, debug):
    """Development trace pages are not an exception to credential redaction."""
    with override_settings(DEBUG=debug):
        response = client.get("/private-secret-path?token=private-query")
    assert response.status_code == 404 and response.content == b"Not Found\n"


def test_host_checked_even_when_view_never_uses_it(client):
    """An attacker-controlled Host cannot reach a plain scaffold endpoint."""
    response = client.get("/", HTTP_HOST="private-invalid.example")
    assert response.status_code == 400
    assert b"private-invalid" not in response.content


def test_untrusted_forwarding_is_not_a_client_identity():
    """Direct peers cannot select the limiter address or request scheme."""
    request = RequestFactory().get(
        "/",
        REMOTE_ADDR="203.0.113.1",
        HTTP_X_FORWARDED_FOR="127.0.0.1",
        HTTP_X_FORWARDED_PROTO="https",
    )
    resolve_client(request)
    assert str(request.client_address) == "203.0.113.1"
    assert not request.is_secure() and not request.internal_request
    assert "HTTP_X_FORWARDED_FOR" not in request.META


@override_settings(
    STEWARDSHIP_PROXY_HOPS=1, STEWARDSHIP_TRUSTED_PROXY_NETWORKS=("10.0.0.0/24",)
)
def test_exact_trusted_hop_sets_address_and_scheme():
    """A configured WSGI peer plus one canonical hop is the sole forwarding input."""
    request = RequestFactory().get(
        "/",
        REMOTE_ADDR="10.0.0.2",
        HTTP_X_FORWARDED_FOR="2001:db8::1",
        HTTP_X_FORWARDED_PROTO="https",
    )
    resolve_client(request)
    assert str(request.client_address) == "2001:db8::1" and request.is_secure()
    assert not request.internal_request


@pytest.mark.parametrize(
    "headers",
    [
        {"HTTP_X_FORWARDED_FOR": "1.2.3.4, 5.6.7.8"},
        {"HTTP_X_FORWARDED_FOR": "bad"},
        {"HTTP_X_FORWARDED_PROTO": "https,http"},
        {"HTTP_FORWARDED": "for=1.2.3.4"},
    ],
)
@override_settings(
    STEWARDSHIP_PROXY_HOPS=1, STEWARDSHIP_TRUSTED_PROXY_NETWORKS=("10.0.0.0/24",)
)
def test_ambiguous_trusted_forwarding_fails_before_the_view(headers):
    """Neither a multi-hop chain nor conflicting formats are guessed into trust."""
    arguments = {
        "REMOTE_ADDR": "10.0.0.2",
        "HTTP_X_FORWARDED_FOR": "1.2.3.4",
        "HTTP_X_FORWARDED_PROTO": "https",
    } | headers
    request = RequestFactory().get("/", **arguments)
    response = SecurityBoundaryMiddleware(lambda _: pytest.fail("View reached"))(
        request
    )
    assert response.status_code == 400


@pytest.mark.parametrize("url", ["/health/live", "/health/ready", "/metrics"])
def test_internal_routes_hidden_from_public_and_forwarded_requests(client, url):
    """Ingress protection also exists in the app, not solely the Caddy matcher."""
    assert client.get(url, REMOTE_ADDR="203.0.113.1").status_code == 404
    assert client.get(url, HTTP_X_FORWARDED_FOR="203.0.113.1").status_code == 404


def test_framework_error_content_is_normalized():
    """A framework trace or CSRF explanation cannot reveal seeded private input."""
    request = RequestFactory().get("/")
    response = SecurityBoundaryMiddleware(
        lambda _: HttpResponse("private-value", status=500)
    )(request)
    assert response.status_code == 500 and b"private-value" not in response.content
