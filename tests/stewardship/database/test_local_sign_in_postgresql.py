"""The LOCAL test sign-in against real PostgreSQL and Valkey (#476).

The route is served through ``local_urls`` with the running profile set to
LOCAL, exactly as ``configure_web`` arranges it. Each test mints a token the
way the operator command does and posts it from a browser client under CSRF
protection; the Google sign-in of ``auth_builders`` is the reference the
resulting session is compared with, since both end in ``establish_identity``.
"""

import time
from dataclasses import replace

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts import local_sign_in
from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.accounts.sessions import revocation_epoch
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.deployment import DeploymentProfile

from .auth_builders import signed_in

pytestmark = pytest.mark.django_db(transaction=True)

PATH = local_sign_in.PATH
HOST = "localhost:8443"
EMAIL = "admin@example.org"


def make_local(settings):
    """Record the LOCAL profile and serve local_urls, as configure_web does."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = DeploymentProfile.LOCAL.value
    settings.ROOT_URLCONF = "parishkit.stewardship.local_urls"


@pytest.fixture
def local(settings, auth_service):
    """Serve the LOCAL URL configuration with the LOCAL profile recorded."""
    make_local(settings)
    return auth_service


def mint(service, email=EMAIL, epoch=None):
    """Issue a token as the operator command does, in the test limiter's namespace."""
    limiter = service.limiter
    return local_sign_in.issue_token(
        limiter.client,
        email,
        revocation_epoch() if epoch is None else epoch,
        namespace=limiter.namespace,
    )


def key_for(service, token):
    """The Valkey key the token's record lives under."""
    return local_sign_in.token_key(token, service.limiter.namespace)


def browser(host=HOST):
    """A CSRF-enforcing browser at the LOCAL origin."""
    return Client(enforce_csrf_checks=True, HTTP_HOST=host)


def post_token(client, token):
    """Open the page as the link does, then post the token with the CSRF form."""
    page = client.get(PATH)
    assert page.status_code == 200
    return client.post(
        PATH,
        {"csrfmiddlewaretoken": client.cookies["pk_admin_csrf"].value, "token": token},
    )


def session_of(client):
    """The server-side session data behind the browser's Admin cookie."""
    return SessionStore(session_key=client.cookies["pk_admin"].value).load()


def test_get_serves_the_form_and_leaves_the_token_in_place(local):
    """The page is CSRF-protected and a GET never consumes the token."""
    token = mint(local)
    client = browser()
    page = client.get(PATH)
    assert page.status_code == 200
    body = page.content.decode()
    assert 'action="/admin/local/sign-in"' in body
    assert "stewardship/local-sign-in-v1.js" in body
    assert "pk_admin_csrf" in client.cookies
    assert page["Content-Security-Policy"].startswith("default-src 'none'")
    ttl = local.limiter.client.ttl(key_for(local, token))
    assert 0 < ttl <= local_sign_in.TOKEN_SECONDS


def test_local_sign_in_produces_the_same_session_as_google(
    settings, auth_service, google
):
    """Both paths end in establish_identity, so the sessions are alike in shape.

    The Google reference signs in first, under the ordinary profile: LOCAL's
    login page hides the Google form, so the order is not arbitrary.
    """
    google_client, google_response = signed_in()
    make_local(settings)
    local = auth_service
    local_client = browser()
    before = local_client.get(PATH).cookies["pk_admin_csrf"].value
    token = mint(local)
    response = post_token(local_client, token)
    assert (response.status_code, response["Location"]) == (
        google_response.status_code,
        google_response["Location"],
    )
    assert response["Location"] == "/admin/"
    # The CSRF secret rotated, as after a Google sign-in.
    assert local_client.cookies["pk_admin_csrf"].value != before
    # The token is spent.
    assert local.limiter.client.exists(key_for(local, token)) == 0
    # One PortalUser per path; the local subject cannot collide with Google's.
    users = {user.google_subject: user for user in PortalUser.objects.all()}
    assert set(users) == {"synthetic-google-subject", "local-test:" + EMAIL}
    tester = users["local-test:" + EMAIL]
    assert (tester.email, tester.hosted_domain, tester.disabled) == (EMAIL, None, False)
    # Same session keys and the same session row shape.
    google_data, local_data = session_of(google_client), session_of(local_client)
    assert set(local_data) == set(google_data)
    assert local_data["principal"] == str(tester.pk)
    assert local_data["recovery_epoch"] == google_data["recovery_epoch"]
    rows = {row.principal_id: row for row in PortalSession.objects.all()}
    assert set(rows) == {users["synthetic-google-subject"].pk, tester.pk}
    for row in rows.values():
        assert row.revoked_at is None
        assert row.authenticated_at <= row.last_activity_at
        assert row.expires_at - row.last_activity_at == (
            rows[tester.pk].expires_at - rows[tester.pk].last_activity_at
        )
    assert rows[tester.pk].session_id == local_client.cookies["pk_admin"].value
    # The same audit record, and both browsers are admitted Admins.
    assert list(
        AuditEvent.objects.filter(actor_id=tester.pk).values_list(
            "event_type", flat=True
        )
    ) == ["admin_login"]
    assert local_client.get("/admin/").status_code == 200
    assert google_client.get("/admin/").status_code == 200


def test_token_is_single_use(local):
    """A second post of the same token is refused and opens no second session."""
    token = mint(local)
    client = browser()
    assert post_token(client, token).status_code == 302
    other = browser()
    assert post_token(other, token).status_code == 403
    assert "pk_admin" not in other.cookies
    assert PortalSession.objects.count() == 1


def test_expired_token_is_refused(local):
    """Once the key has expired the link is as good as unknown."""
    token = mint(local)
    local.limiter.client.pexpire(key_for(local, token), 1)
    time.sleep(0.05)
    assert post_token(browser(), token).status_code == 403
    assert PortalSession.objects.count() == 0


def test_unauthorized_email_is_refused_by_the_login_rules(local):
    """An address no rule names gets no session, as with Google."""
    token = mint(local, email="stranger@example.org")
    assert post_token(browser(), token).status_code == 403
    assert PortalSession.objects.count() == 0
    # The token was still consumed: a refusal does not leave it usable.
    assert local.limiter.client.exists(key_for(local, token)) == 0


def test_stale_recovery_epoch_is_refused(local):
    """A link minted under an earlier epoch is refused like stale OAuth state."""
    token = mint(local, epoch="before-recovery")
    assert post_token(browser(), token).status_code == 403
    assert PortalSession.objects.count() == 0


def test_disabled_user_is_refused(local):
    """Disabling the PortalUser refuses the next link, exactly as for Google."""
    client = browser()
    assert post_token(client, mint(local)).status_code == 302
    PortalUser.objects.filter(google_subject="local-test:" + EMAIL).update(
        disabled=True, version=F("version") + 1
    )
    assert post_token(browser(), mint(local)).status_code == 403


@pytest.mark.parametrize("token", ["", "short", "x" * 43, "!" * 43, "y" * 44])
def test_unknown_or_malformed_tokens_are_refused(local, token):
    """Garbage and well-formed but unknown tokens are refused alike."""
    assert post_token(browser(), token).status_code == 403
    assert PortalSession.objects.count() == 0


def test_second_sign_in_is_a_step_up_like_google(local):
    """A new link from a signed-in browser refreshes that session in place."""
    client = browser()
    assert post_token(client, mint(local)).status_code == 302
    cookie = client.cookies["pk_admin"].value
    row = PortalSession.objects.get()
    assert post_token(client, mint(local)).status_code == 302
    assert client.cookies["pk_admin"].value == cookie
    assert PortalSession.objects.get().pk == row.pk
    # Audit keys are not sequential, so compare the set of recorded kinds.
    assert sorted(
        AuditEvent.objects.filter(actor_id=row.principal_id).values_list(
            "event_type", flat=True
        )
    ) == ["admin_login", "admin_step_up"]


def test_csrf_protects_the_post(local):
    """A post without the CSRF form is refused and leaves the token in place."""
    token = mint(local)
    client = browser()
    assert client.get(PATH).status_code == 200
    assert client.post(PATH, {"token": token}).status_code == 403
    assert local.limiter.client.exists(key_for(local, token)) == 1


def test_wrong_host_is_refused_without_consuming(local):
    """A Host other than the LOCAL origin is refused before Valkey is read."""
    token = mint(local)
    assert post_token(browser("127.0.0.1:8443"), token).status_code == 403
    assert local.limiter.client.exists(key_for(local, token)) == 1


@pytest.mark.parametrize(
    "profile",
    [
        DeploymentProfile.PRODUCTION,
        DeploymentProfile.DEVELOPMENT,
        DeploymentProfile.TEST,
    ],
)
def test_outside_local_the_view_answers_404_and_keeps_the_token(
    local, settings, profile
):
    """Even with the route present, a non-LOCAL profile gets 404 and no sign-in."""
    settings.STEWARDSHIP_DEPLOYMENT_PROFILE = profile.value
    token = mint(local)
    # No CSRF enforcement here, so the POST reaches the view's own refusal.
    client = Client(HTTP_HOST=HOST)
    assert client.get(PATH).status_code == 404
    assert client.post(PATH, {"token": token}).status_code == 404
    assert local.limiter.client.exists(key_for(local, token)) == 1
    assert PortalSession.objects.count() == 0


def test_sign_in_works_before_setup_completes_and_leads_to_the_wizard(
    settings, auth_service
):
    """Before setup, the gate lets the route through and /admin/ goes to the wizard.

    The developer signs in with the first Admin's email rule to run the real
    setup wizard; the route is one of the gate's authentication routes, so
    it is not redirected to /admin/login while setup is incomplete.
    """
    make_local(settings)
    settings.STEWARDSHIP_AUTH_RUNTIME = replace(
        auth_service, setup_complete=lambda: False
    )
    client = browser()
    assert client.get("/admin/").status_code == 302
    assert client.get("/admin/")["Location"] == "/admin/login"
    response = post_token(client, mint(auth_service))
    assert (response.status_code, response["Location"]) == (302, "/admin/")
    assert PortalSession.objects.count() == 1
    home = client.get("/admin/")
    assert (home.status_code, home["Location"]) == (302, "/admin/setup")


def test_google_sign_in_is_unchanged_under_local_urls(settings, auth_service, google):
    """local_urls leaves the Google route intact; LOCAL's login page hides it."""
    settings.ROOT_URLCONF = "parishkit.stewardship.local_urls"
    client, response = signed_in()
    assert (response.status_code, response["Location"]) == (302, "/admin/")
    assert client.get("/admin/").status_code == 200
    assert PortalUser.objects.get().google_subject == "synthetic-google-subject"
    # With LOCAL recorded, the login page offers no Google form at all.
    make_local(settings)
    page = browser().get("/admin/login")
    assert page.status_code == 200
    assert b"Sign in with Google" not in page.content
    assert b"not available in the local laptop environment" in page.content
