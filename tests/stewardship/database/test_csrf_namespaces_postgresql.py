"""Family and Admin CSRF state is as separate as their session cookies."""

import re

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import Client

from parishkit.stewardship.accounts.sessions import NamespacedCsrfMiddleware

from .auth_builders import start
from .test_family_auth_postgresql import family_service as family_service
from .test_family_auth_postgresql import login

pytestmark = pytest.mark.django_db(transaction=True)
JSON = {"content_type": "application/json", "HTTP_ACCEPT": "application/json"}


def page_token(client, path):
    """Read the token a real page renders, as the browser scripts do."""
    response = client.get(path)
    assert response.status_code == 200
    return re.search(
        r'name="csrfmiddlewaretoken" value="([^"]+)"', response.content.decode()
    ).group(1)


def admin_login(client):
    """Complete the ordinary Google sign-in inside an existing browser."""
    query = start(client)
    response = client.get(
        "/admin/oauth/callback", {"code": "synthetic-code", "state": query["state"][0]}
    )
    assert response.status_code == 302
    assert "pk_admin" in client.cookies


def keepalive(client, token):
    """The Family tab's empty CSRF-protected activity claim."""
    return client.post("/family/keepalive", b"", HTTP_X_CSRFTOKEN=token, **JSON)


def keepalive_from(client, token, origin):
    """A keepalive carrying an explicit browser Origin header."""
    return client.post(
        "/family/keepalive", b"", HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN=origin, **JSON
    )


def test_family_page_token_survives_admin_login(family_service, google):
    """A staff helper's sign-in in the same browser leaves the Family tab working."""
    client, response = login(family_service.code)
    assert response.status_code == 302
    token = page_token(client, "/family/")
    family_secret = client.cookies["pk_family_csrf"].value
    admin_login(client)
    assert client.cookies["pk_family_csrf"].value == family_secret
    assert client.cookies["pk_admin_csrf"].value != family_secret
    assert keepalive(client, token).status_code == 200


def test_admin_page_token_survives_family_login(family_service, google):
    """A Family sign-in in the same browser leaves open Admin pages working."""
    client = Client(enforce_csrf_checks=True)
    admin_login(client)
    token = page_token(client, "/admin/")
    admin_secret = client.cookies["pk_admin_csrf"].value
    _, response = login(family_service.code, client)
    assert response.status_code == 302
    assert client.cookies["pk_admin_csrf"].value == admin_secret
    logout = client.post("/admin/logout", {"csrfmiddlewaretoken": token})
    assert logout.status_code == 302


def test_login_still_rotates_its_own_namespace_token(family_service, google):
    """A pre-login token cannot be replayed after sign-in within one namespace."""
    client = Client(enforce_csrf_checks=True)
    assert client.get("/").status_code == 200
    before = client.cookies["pk_family_csrf"].value
    response = client.post(
        "/", {"code": family_service.code, "csrfmiddlewaretoken": before}
    )
    assert response.status_code == 302
    assert client.cookies["pk_family_csrf"].value != before
    assert keepalive(client, before).status_code == 403
    assert client.get("/admin/login").status_code == 200
    anonymous = client.cookies["pk_admin_csrf"].value
    admin_login(client)
    assert client.cookies["pk_admin_csrf"].value != anonymous
    stale = client.post("/admin/logout", {"csrfmiddlewaretoken": anonymous})
    assert stale.status_code == 403


def test_family_csrf_failure_is_distinguishable_and_recoverable(family_service):
    """A stale Family token gets a fresh one; other failures keep the plain 403."""
    client, _ = login(family_service.code)
    response = client.post("/family/form", b"{}", HTTP_X_CSRFTOKEN="x" * 64, **JSON)
    assert response.status_code == 403
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert body["error"] == "csrf_failed"
    assert keepalive(client, body["csrf_token"]).status_code == 200
    plain = client.post("/family/keepalive", HTTP_X_CSRFTOKEN="x" * 64)
    assert plain.status_code == 403
    assert plain.content == b"Access is not authorized.\n"
    admin = client.post("/admin/logout", HTTP_X_CSRFTOKEN="x" * 64, **JSON)
    assert admin.status_code == 403
    assert admin.content == b"Access is not authorized.\n"


def test_family_origin_failure_is_not_recoverable(family_service):
    """A fresh token cannot fix a foreign Origin, so it stays a plain 403."""
    client, _ = login(family_service.code)
    token = page_token(client, "/family/")
    foreign = keepalive_from(client, token, "https://attacker.example")
    assert foreign.status_code == 403
    assert foreign.content == b"Access is not authorized.\n"
    missing = client.post("/family/keepalive", b"", **JSON)
    assert missing.status_code == 403
    assert missing.json()["error"] == "csrf_failed"


def test_real_family_session_end_is_not_a_csrf_failure(family_service):
    """With a valid token, an ended session still reports session_ended."""
    client, _ = login(family_service.code)
    token = page_token(client, "/family/")
    assert client.post("/family/logout", {"csrfmiddlewaretoken": token}).status_code
    body = b'{"testing_acknowledged": true}'
    response = client.post("/family/form", body, HTTP_X_CSRFTOKEN=token, **JSON)
    assert response.status_code == 403
    assert response.json() == {"error": "session_ended"}


def test_malformed_namespace_cookie_is_replaced(family_service):
    """An injected malformed secret is never trusted; a well-formed one replaces it."""
    client, _ = login(family_service.code)
    client.cookies["pk_family_csrf"] = "not-a-secret"
    assert client.get("/").status_code in {200, 302}
    secret = client.cookies["pk_family_csrf"].value
    assert re.fullmatch(r"[A-Za-z0-9]{32}", secret)


@pytest.mark.parametrize("secure", [False, True])
def test_namespace_cookie_attributes(family_service, settings, secure):
    """The test cookie jar ignores Path, so assert each Set-Cookie directly."""
    settings.CSRF_COOKIE_SECURE = secure
    settings.CSRF_COOKIE_DOMAIN = "testserver" if secure else None
    for path, name, scope in (
        ("/", "pk_family_csrf", "/"),
        ("/admin/login", "pk_admin_csrf", "/admin/"),
    ):
        response = Client().get(path)
        assert response.status_code == 200
        cookie = response.cookies[name]
        assert cookie["path"] == scope
        assert cookie["httponly"] is True
        assert cookie["samesite"] == "Lax"
        assert bool(cookie["secure"]) is secure
        assert cookie["domain"] == ("testserver" if secure else "")
        assert "csrftoken" not in response.cookies


@pytest.mark.parametrize(
    "override",
    [
        {"CSRF_USE_SESSIONS": True},
        {"CSRF_COOKIE_NAME": "other"},
        {"CSRF_COOKIE_PATH": "/family/"},
    ],
)
def test_unsupported_csrf_settings_refuse_to_load(settings, override):
    """Settings the namespaced cookies would silently ignore fail at load time."""
    for key, value in override.items():
        setattr(settings, key, value)
    with pytest.raises(ImproperlyConfigured):
        NamespacedCsrfMiddleware(lambda request: None)
