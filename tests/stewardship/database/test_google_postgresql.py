"""Google cryptographic claims, durable sessions, early limits and namespace tests."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.contrib.sessions.models import Session
from django.db import connections
from django.db.models import F
from django.test import Client

from parishkit.stewardship.accounts.models import PortalSession, PortalUser
from parishkit.stewardship.accounts.sessions import cleanup_admin_sessions
from parishkit.stewardship.audit.models import AuditEvent

from .auth_builders import OMIT, signed_in, start, unguarded

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
    assert len(query["scope"]) == 1
    assert set(query["scope"][0].split()) == {"openid", "email"}
    assert query["max_age"] == ["0"]
    assert json.loads(query["claims"][0]) == {
        "id_token": {"auth_time": {"essential": True}}
    }
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
    assert row.expires_at - row.last_activity_at == timedelta(hours=12)
    assert PortalUser.objects.get().google_subject == "synthetic-google-subject"
    assert client.get("/admin/").status_code == 200
    data = Session.objects.get(pk=row.session_id).get_decoded()
    assert set(data) == {
        "principal",
        "recovery_epoch",
        "authority_fingerprint",
        "_session_expiry",
    }
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
        {"auth_time": None},
        {"auth_time": True},
        {"auth_time": "123"},
        {"auth_time": 0},
        {"auth_time": 10**30},
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


def test_freshness_uses_signed_authentication_not_callback_time(auth_service, google):
    """An older signed instant is retained; a new callback cannot reset its age."""
    from parishkit.stewardship.accounts.sessions import database_now

    instant = int(database_now().timestamp()) - 20
    google[0]["auth_time"] = instant
    _, response = signed_in()
    assert response.status_code == 302
    assert PortalSession.objects.get().authenticated_at.timestamp() == instant


@pytest.mark.parametrize("age", [301, 86400])
def test_existing_google_session_allows_login_without_privileged_freshness(
    auth_service, google, age
):
    """Ordinary SSO remains usable without minting fresh privileged authority."""
    from django.test import RequestFactory

    from parishkit.stewardship.accounts.sessions import database_now, require_fresh

    instant = int(database_now().timestamp()) - age
    google[0]["auth_time"] = instant
    browser, response = signed_in()
    assert response.status_code == 302
    row = PortalSession.objects.get()
    assert row.authenticated_at.timestamp() == instant
    assert row.expires_at - row.last_activity_at == timedelta(hours=12)
    assert browser.get("/admin/").status_code == 200
    request = RequestFactory().get("/admin/")
    request.portal_session = row
    with pytest.raises(PermissionError):
        require_fresh(request)


def test_missing_auth_time_uses_the_nonce_bound_issue_time(auth_service, google):
    """Google never sends auth_time; this sign-in's issued token is the fresh trip."""
    from django.test import RequestFactory

    from parishkit.stewardship.accounts.sessions import require_fresh

    google[0]["auth_time"] = OMIT
    _, response = signed_in()
    assert response.status_code == 302
    row = PortalSession.objects.get()
    request = RequestFactory().get("/admin/")
    request.portal_session = row
    assert require_fresh(request) == row.authenticated_at


def test_future_google_authentication_is_denied(auth_service, google):
    """Provider clock skew cannot manufacture future privileged authority."""
    from parishkit.stewardship.accounts.sessions import database_now

    google[0]["auth_time"] = int(database_now().timestamp()) + 60
    _, response = signed_in()
    assert response.status_code == 403


def test_google_callback_configuration_gap_is_retryable(
    auth_service, google, monkeypatch
):
    """A file/SQL activation mismatch returns the sealed retry page, not a 500."""
    from parishkit.config import ConfigError
    from parishkit.stewardship.accounts import authentication

    def unavailable(*args):
        raise ConfigError("Synthetic configuration activation gap")

    monkeypatch.setattr(authentication, "current_principal", unavailable)
    _, response = signed_in()
    assert response.status_code == 503
    assert response["Retry-After"] == "5"
    assert not PortalSession.objects.exists()


def test_missing_policy_epoch_returns_retryable_denial(
    auth_service, google, monkeypatch
):
    """A pre-bootstrap callback cannot construct an invalid identity counter."""
    from parishkit.stewardship.accounts import authentication

    class EmptyEpochs:
        """Model an absent bootstrap epoch without deleting immutable SQL history."""

        def order_by(self, *args):
            return self

        def values_list(self, *args, **kwargs):
            return self

        def first(self):
            return None

    monkeypatch.setattr(authentication.PolicyEpoch, "objects", EmptyEpochs())
    _, response = signed_in()
    assert response.status_code == 503
    assert not PortalSession.objects.exists()


def test_signed_denied_account_is_not_authorized(auth_service, google):
    """A verified personal identity has no implicit parish role."""
    google[0]["email"] = "outsider@example.net"
    _, response = signed_in()
    assert response.status_code == 403
    assert not PortalSession.objects.exists()


def refusals():
    """Each named refusal's actor address, oldest first (#953)."""
    return [
        PortalUser.objects.get(pk=event.actor_id).email
        for event in AuditEvent.objects.filter(
            event_type="admin_login_refused"
        ).order_by("created_at", "id")
    ]


def test_refused_account_is_named_once_per_window(auth_service, google, monkeypatch):
    """A verified account policy refuses is named, bounded per account (#953).

    It replaces the anonymous sample, carries no context, and a second
    account gets its own entry inside the first one's window.
    """
    from parishkit.stewardship.accounts import auth_incidents
    from parishkit.stewardship.audit.models import AuditContext

    google[0].update(email="outsider@example.net", sub="outsider-subject")
    assert signed_in()[1].status_code == 403
    assert signed_in()[1].status_code == 403
    assert refusals() == ["outsider@example.net"]
    refused = AuditEvent.objects.get(event_type="admin_login_refused")
    assert refused.subject_id is None
    assert not AuditContext.objects.filter(event=refused).exists()
    assert not AuditEvent.objects.filter(event_type="admin_login_denied").exists()
    google[0].update(email="stranger@example.net", sub="stranger-subject")
    assert signed_in()[1].status_code == 403
    assert refusals() == ["outsider@example.net", "stranger@example.net"]
    # Once the window has passed, the same account is named again.
    monkeypatch.setattr(auth_incidents, "ACCOUNT_REFUSAL_INTERVAL", timedelta(0))
    google[0].update(email="outsider@example.net", sub="outsider-subject")
    assert signed_in()[1].status_code == 403
    assert refusals() == [
        "outsider@example.net",
        "stranger@example.net",
        "outsider@example.net",
    ]
    assert not PortalSession.objects.exists()


def test_disabled_identity_is_a_named_refusal(auth_service, google):
    """A disabled identity with an Administrator rule is refused and named."""
    signed_in()
    PortalUser.objects.update(disabled=True, version=F("version") + 1)
    assert signed_in()[1].status_code == 403
    assert refusals() == ["admin@example.org"]


def test_concurrent_refusals_of_one_account_write_one_entry(auth_service, google):
    """Two refusals of one account racing on separate connections name it once.

    The deployment-wide advisory lock serializes the check and the insert, so
    exactly one of the two writes the entry (#968).
    """
    from parishkit.stewardship.accounts.auth_incidents import record_account_refusal

    signed_in()
    user_id = PortalUser.objects.get().pk
    barrier = Barrier(2)

    def refuse():
        """Start together, then refuse the same account on a new connection."""
        try:
            barrier.wait(10)
            return record_account_refusal(user_id)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result(timeout=20) for f in [pool.submit(refuse) for _ in "ab"]]
    assert sorted(results) == [False, True]
    assert refusals() == ["admin@example.org"]


def test_refusals_past_the_ceiling_become_anonymous_samples(
    auth_service, google, monkeypatch
):
    """Cheap Google accounts cannot flood the log: past the deployment-wide
    ceiling a refusal falls back to the anonymous sample (#968)."""
    from parishkit.stewardship.accounts import auth_incidents

    monkeypatch.setattr(auth_incidents, "ACCOUNT_REFUSAL_CEILING", 2)
    for name in ("one", "two", "three", "four"):
        google[0].update(email=f"{name}@example.net", sub=f"{name}-subject")
        assert signed_in()[1].status_code == 403
    assert refusals() == ["one@example.net", "two@example.net"]
    # The sample keeps its own five-minute bound and names no one.
    denied = AuditEvent.objects.get(event_type="admin_login_denied")
    assert denied.actor_id is None


def test_refusal_audit_outage_is_retryable_after_counting(
    auth_service, google, monkeypatch
):
    """A database failure naming a refusal is the retryable 503, and the
    limiter has already counted the attempt (#968)."""
    from django.db import DatabaseError

    from parishkit.stewardship.accounts import auth_incidents, authentication

    class Broken:
        """An audit table whose every read fails as an outage would."""

        class objects:  # noqa: N801 - mirrors the model manager attribute
            @staticmethod
            def filter(**kwargs):
                raise DatabaseError("Synthetic audit outage")

    counted = []
    record_failure = authentication.record_failure

    def counting(*args, **kwargs):
        """Note that the limiter accounting ran, then run it."""
        counted.append(kwargs["identity"])
        return record_failure(*args, **kwargs)

    monkeypatch.setattr(authentication, "record_failure", counting)
    monkeypatch.setattr(auth_incidents, "AuditEvent", Broken)
    google[0].update(email="outsider@example.net", sub="outsider-subject")
    _, response = signed_in()
    assert response.status_code == 503
    assert not PortalSession.objects.exists()
    assert len(counted) == 1 and counted[0]


def test_identity_limited_attempt_stays_an_anonymous_sample(
    auth_service, google, monkeypatch
):
    """An attempt the per-identity limiter stops before policy runs names no
    one, even for an account policy would refuse (#968)."""
    limiter = auth_service.limiter
    checked = limiter.counters

    def limited(counters, *, failure=False):
        """Report the identity window as exhausted; count everything else."""
        if not failure and counters[0].name.startswith("admin_identity_"):
            return 30
        return checked(counters, failure=failure)

    monkeypatch.setattr(limiter, "counters", limited)
    google[0].update(email="outsider@example.net", sub="outsider-subject")
    assert signed_in()[1].status_code == 429
    assert not refusals()
    assert AuditEvent.objects.get(event_type="admin_login_denied").actor_id is None


def test_anonymous_refusal_stays_a_sample_with_no_actor(auth_service, google):
    """A replayed sign-in state names no one: the sampled record (#953)."""
    client = Client(enforce_csrf_checks=True)
    query = start(client)
    data = {"code": "synthetic", "state": query["state"][0]}
    assert client.get("/admin/oauth/callback", data).status_code == 302
    assert client.get("/admin/oauth/callback", data).status_code == 403
    denied = AuditEvent.objects.get(event_type="admin_login_denied")
    assert denied.actor_id is None
    assert not refusals()


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
        "/admin/logout", {"csrfmiddlewaretoken": client.cookies["pk_admin_csrf"].value}
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


@pytest.mark.parametrize("absolute", [False, True])
def test_admin_idle_and_absolute_boundaries_and_fresh_auth(
    auth_service, google, monkeypatch, absolute
):
    from django.test import RequestFactory

    from parishkit.stewardship.accounts import sessions

    browser, _ = signed_in()
    row = PortalSession.objects.get()
    request = RequestFactory().get("/admin/")
    request.portal_session = row
    monkeypatch.setattr(
        sessions, "database_now", lambda: row.authenticated_at + timedelta(minutes=5)
    )
    assert sessions.require_fresh(request) == row.authenticated_at
    monkeypatch.setattr(
        sessions,
        "database_now",
        lambda: row.authenticated_at + timedelta(minutes=5, microseconds=1),
    )
    with pytest.raises(PermissionError):
        sessions.require_fresh(request)
    if absolute:
        row.last_activity_at = row.expires_at - timedelta(minutes=5)
        # Future activity is refused by the session guard (#306); seed it.
        with unguarded():
            PortalSession.objects.filter(pk=row.pk).update(
                last_activity_at=row.last_activity_at,
                version=F("version") + 1,
            )
    boundary = (
        row.expires_at if absolute else row.last_activity_at + sessions.ADMIN_IDLE
    )
    monkeypatch.setattr(sessions, "database_now", lambda: boundary)
    assert browser.get("/admin/").status_code == 302
    row.refresh_from_db()
    assert row.revoked_at is not None
    assert sessions.cleanup_admin_sessions() == 1


def test_anonymous_cleanup_preserves_live_protected_sessions(auth_service, google):
    from django.contrib.sessions.backends.db import SessionStore
    from django.utils import timezone

    from parishkit.stewardship.accounts.auth_models import OAuthStateConsumption
    from parishkit.stewardship.accounts.sessions import cleanup_anonymous_sessions

    _, _ = signed_in()
    live = PortalSession.objects.get()
    expired = SessionStore()
    expired["oauth_state"] = "synthetic-state"
    expired.set_expiry(timezone.now() - timedelta(seconds=1))
    expired.save()
    OAuthStateConsumption.objects.create(
        fingerprint="a" * 64, expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert cleanup_anonymous_sessions() == (1, 1)
    assert Session.objects.filter(pk=live.session_id).exists()
    assert not Session.objects.filter(pk=expired.session_key).exists()


def test_changed_privileges_rotate_cookie_without_refreshing_authentication(
    auth_service, google, monkeypatch
):
    """A newly authorized scope cannot reuse the old session or its CSRF token."""
    from parishkit.stewardship.accounts import sessions
    from parishkit.stewardship.accounts.policy import Principal

    browser, _ = signed_in()
    original = PortalSession.objects.get()
    cookie = browser.cookies["pk_admin"].value
    csrf = browser.cookies["pk_admin_csrf"].value
    principal = Principal(original.principal_id, frozenset({"staff"}))
    monkeypatch.setattr(sessions, "current_principal", lambda *args: principal)
    assert browser.get("/admin/").status_code == 200
    assert browser.cookies["pk_admin"].value != cookie
    assert browser.cookies["pk_admin_csrf"].value != csrf
    replacement = PortalSession.objects.get(revoked_at__isnull=True)
    assert replacement.authenticated_at == original.authenticated_at
    assert replacement.expires_at == original.expires_at
    original.refresh_from_db()
    assert original.revoked_at is not None
    stale = Client()
    stale.cookies["pk_admin"] = cookie
    assert stale.get("/admin/").status_code == 302
    assert AuditEvent.objects.filter(event_type="admin_privileges_changed").count() == 1
    current_cookie = browser.cookies["pk_admin"].value
    assert browser.get("/admin/").status_code == 200
    assert browser.cookies["pk_admin"].value == current_cookie


def test_read_guard_never_rotates_privilege_transition(
    auth_service, google, monkeypatch
):
    """Streaming reauthorization fails closed if its prior admission became stale."""
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    from parishkit.stewardship.accounts import sessions
    from parishkit.stewardship.accounts.policy import Principal

    browser, _ = signed_in()
    row = PortalSession.objects.get()
    principal = Principal(row.principal_id, frozenset({"staff"}))
    monkeypatch.setattr(sessions, "current_principal", lambda *args: principal)
    request = RequestFactory().get("/admin/")
    request.session = SessionStore(browser.cookies["pk_admin"].value)
    assert (
        sessions.authenticated_admin(request, store=auth_service.store, read_only=True)
        is None
    )
    assert PortalSession.objects.count() == 1
    row.refresh_from_db()
    assert row.revoked_at is None


def test_normal_admin_routing_does_not_repeat_session_authorization(
    auth_service, google, monkeypatch
):
    """The availability gate leaves normal expiry/rotation to the owning view."""
    from unittest.mock import Mock

    from parishkit.stewardship.accounts import sessions

    browser, _ = signed_in()
    checked = Mock(wraps=sessions.current_principal)
    monkeypatch.setattr(sessions, "current_principal", checked)
    version = PortalSession.objects.get().version
    assert browser.get("/admin/").status_code == 200
    # The dashboard has one ordinary admission and one post-query read guard.
    # The availability middleware and presentation context do neither again.
    assert checked.call_count == 2
    assert PortalSession.objects.get().version == version + 1


def test_step_up_refreshes_the_current_session_in_place(auth_service, google):
    """The same Admin signing in again keeps its row, key and session-bound work."""
    from django.test import RequestFactory

    from parishkit.stewardship.accounts.sessions import database_now, require_fresh

    google[0]["auth_time"] = int(database_now().timestamp()) - 600
    browser, _ = signed_in()
    original = PortalSession.objects.get()
    cookie = browser.cookies["pk_admin"].value
    csrf = browser.cookies["pk_admin_csrf"].value
    request = RequestFactory().get("/admin/")
    request.portal_session = original
    with pytest.raises(PermissionError):
        require_fresh(request)
    del google[0]["auth_time"]
    _, response = signed_in(browser, next="/admin/setup/credentials/parishsoft")
    assert response.status_code == 302
    assert response["Location"] == "/admin/setup/credentials/parishsoft"
    row = PortalSession.objects.get()
    assert (row.pk, row.session_id) == (original.pk, original.session_id)
    assert browser.cookies["pk_admin"].value == cookie
    assert browser.cookies["pk_admin_csrf"].value != csrf
    assert row.authenticated_at > original.authenticated_at
    assert row.last_activity_at >= row.authenticated_at
    assert row.expires_at == original.expires_at
    assert row.revoked_at is None and row.version > original.version
    request.portal_session = row
    assert require_fresh(request) == row.authenticated_at
    assert AuditEvent.objects.filter(event_type="admin_login").count() == 1
    assert (
        AuditEvent.objects.filter(event_type="admin_step_up", subject_id=row.pk).count()
        == 1
    )
    assert browser.get("/admin/").status_code == 200


def test_step_up_never_moves_freshness_backwards(auth_service, google):
    """An older signed Google instant leaves the session's freshness unchanged."""
    browser, _ = signed_in()
    original = PortalSession.objects.get()
    google[0]["auth_time"] = int(original.authenticated_at.timestamp()) - 60
    _, response = signed_in(browser)
    assert response.status_code == 302
    row = PortalSession.objects.get()
    assert row.pk == original.pk
    assert row.authenticated_at == original.authenticated_at


def test_different_google_account_replaces_the_session(auth_service, google):
    """Another account from the same browser is a new login, never a step-up."""
    browser, _ = signed_in()
    original = PortalSession.objects.get()
    cookie = browser.cookies["pk_admin"].value
    google[0]["sub"] = "synthetic-second-google-subject"
    _, response = signed_in(browser)
    assert response.status_code == 302
    original.refresh_from_db()
    assert original.revoked_at is not None
    replacement = PortalSession.objects.get(revoked_at__isnull=True)
    assert replacement.principal_id != original.principal_id
    assert browser.cookies["pk_admin"].value != cookie
    assert AuditEvent.objects.filter(event_type="admin_login").count() == 2
    assert not AuditEvent.objects.filter(event_type="admin_step_up").exists()


def test_idle_session_is_replaced_rather_than_revived(auth_service, google):
    """Step-up cannot resurrect a session that already reached its idle limit."""
    from parishkit.stewardship.accounts.sessions import database_now

    google[0]["auth_time"] = int(database_now().timestamp()) - 7200
    browser, _ = signed_in()
    original = PortalSession.objects.get()
    PortalSession.objects.filter(pk=original.pk).update(
        last_activity_at=original.authenticated_at + timedelta(minutes=1),
        version=F("version") + 1,
    )
    del google[0]["auth_time"]
    _, response = signed_in(browser)
    assert response.status_code == 302
    original.refresh_from_db()
    assert original.revoked_at is not None
    assert PortalSession.objects.filter(revoked_at__isnull=True).count() == 1
    assert AuditEvent.objects.filter(event_type="admin_timeout").count() == 1
    assert AuditEvent.objects.filter(event_type="admin_login").count() == 2
    assert not AuditEvent.objects.filter(event_type="admin_step_up").exists()


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "/admin/"),
        ("/admin/integrations", "/admin/integrations"),
        ("/admin/setup/credentials/slack", "/admin/setup/credentials/slack"),
        ("https://attacker.example/admin/", "/admin/"),
        ("//attacker.example/admin/", "/admin/"),
    ],
)
def test_sign_in_returns_only_to_a_validated_admin_path(
    auth_service, google, value, expected
):
    """The next field survives OAuth state only as a same-origin Admin path."""
    fields = {} if value is None else {"next": value}
    _, response = signed_in(**fields)
    assert response.status_code == 302
    assert response["Location"] == expected
