"""Second-review checks across actual SQL, signed identity and private sessions."""

import json
from datetime import timedelta
from importlib import import_module
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.contrib.sessions.models import Session
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.http import HttpResponse
from django.test import Client, RequestFactory

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.authentication import GoogleCallback
from parishkit.stewardship.accounts.credential_database import admit_web_staging_grants
from parishkit.stewardship.accounts.cryptography import KEY_ID
from parishkit.stewardship.accounts.limiting import LimiterUnavailable
from parishkit.stewardship.accounts.models import PortalSession
from parishkit.stewardship.accounts.secret_models import (
    SECRET_TARGETS,
    SecretReplacementRequest,
)
from parishkit.stewardship.accounts.secret_requests import (
    cancel_secret_request,
    clean_secret_request,
)
from parishkit.stewardship.accounts.sessions import (
    NamespacedSessionMiddleware,
    database_now,
)
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.audit.services import operational
from parishkit.stewardship.observability import Event
from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS

from .auth_builders import signed_in, start
from .test_credential_isolation_postgresql import (  # noqa: F401
    identity,
    isolated_roles,
    stage,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("event", list(Event))
def test_all_operational_events_are_admitted_by_sql(event):
    """New Python events cannot drift from the database's closed event set."""
    assert operational(event).event == event.value


@pytest.mark.parametrize("target", sorted(SECRET_TARGETS))
def test_sql_consumer_vocabulary_matches_service_mounts(target):
    """Every target has identical required consumers in Python and PostgreSQL."""
    expected = {
        role.value for role, names in ALLOWED_SECRETS.items() if target in names
    }
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT jsonb_array_elements_text(stewardship_credential_consumers_v1(%s))",
            [target],
        )
        assert {row[0] for row in cursor.fetchall()} == expected


@pytest.mark.parametrize("kid", ["_2026-09", "-rotation", "a_B-9", "a.b", "", "a" * 49])
def test_sealed_key_id_sql_matches_python_grammar(kid):
    """Operator key IDs accepted by the keyring also pass sealed SQL admission."""
    envelope = json.dumps({"v": 1, "alg": "sealedbox-v1", "kid": kid, "body": "YQ"})
    with connection.cursor() as cursor:
        cursor.execute("SELECT stewardship_valid_sealed_candidate_v1(%s)", [envelope])
        assert cursor.fetchone()[0] == bool(KEY_ID.fullmatch(kid))


@pytest.mark.usefixtures("isolated_roles")
def test_legacy_cleanup_cannot_consume_cancelled_sealed_request():
    """The old deletion callback never touches installer-owned sealed staging."""
    identifier, _ = stage()
    row = SecretReplacementRequest.objects.get(pk=identifier)
    cancel_secret_request(
        request_id=identifier, actor_id=row.requested_by_id, correlation_id=uuid4()
    )
    callback = Mock()
    with pytest.raises(ConfigError, match="target installer"):
        clean_secret_request(
            request_id=identifier,
            target=row.target,
            correlation_id=uuid4(),
            remove_payload=callback,
        )
    callback.assert_not_called()
    assert (
        SecretReplacementRequest.objects.get(pk=identifier).state == "cleanup_pending"
    )


@pytest.mark.parametrize("offset", [None, True, -901, 60])
def test_google_callback_rejects_invalid_server_initiation(
    auth_service, google, monkeypatch, offset
):
    """The server-side initiation bound is independent of JWT signature validity."""
    original = GoogleCallback._get_state

    def altered(self, request, provider):
        """Model a stale or corrupt durable OAuth state after one-use admission."""
        state, response = original(self, request, provider)
        state["data"]["initiated_at"] = (
            offset
            if type(offset) is not int
            else int(database_now().timestamp()) + offset
        )
        return state, response

    monkeypatch.setattr(GoogleCallback, "_get_state", altered)
    _, response = signed_in()
    assert response.status_code == 403 and not PortalSession.objects.exists()


def test_signed_authentication_cannot_postdate_token_issuance(auth_service, google):
    """A validly signed token cannot assert authentication later than its issuance."""
    google[0]["iat"] = int(database_now().timestamp()) - 60
    _, response = signed_in()
    assert response.status_code == 403 and not PortalSession.objects.exists()


def test_anonymous_oauth_expiry_is_short_without_shortening_reauthentication(
    auth_service, google
):
    """Abandoned starts are short-lived; an existing Admin keeps its fixed deadline."""
    client = Client(enforce_csrf_checks=True)
    start(client)
    anonymous = Session.objects.get(pk=client.cookies["pk_admin"].value)
    assert (
        timedelta(minutes=14)
        < anonymous.expire_date - database_now()
        <= timedelta(minutes=15)
    )
    browser, _ = signed_in()
    row = PortalSession.objects.get()
    start(browser)
    assert Session.objects.get(pk=row.session_id).expire_date == row.expires_at


def test_modified_admin_session_under_script_name_sets_admin_cookie():
    """Exercise the actual Set-Cookie path, not just absence of a Family cookie."""
    request = RequestFactory().get("/admin/login", SCRIPT_NAME="/prefix")

    def mutate(value):
        value.session["synthetic"] = True
        return HttpResponse("ok")

    response = NamespacedSessionMiddleware(mutate)(request)
    assert response.cookies["pk_admin"]["path"] == "/admin/"
    assert "pk_family" not in response.cookies


@pytest.mark.usefixtures("isolated_roles")
def test_web_ciphertext_grant_admission_rejects_excess():
    """A mistaken table-level SELECT cannot masquerade as metadata-only access."""
    with identity("pk_stewardship_web"):
        admit_web_staging_grants()
    with connection.cursor() as cursor:
        cursor.execute(
            "GRANT SELECT ON stewardship_sealed_credential_staging "
            "TO pk_stewardship_web"
        )
    with identity("pk_stewardship_web"), pytest.raises(ConfigError, match="ciphertext"):
        admit_web_staging_grants()


@pytest.mark.usefixtures("isolated_roles")
def test_downgrade_history_guard_sees_rows_as_non_superuser_owner():
    """FORCE RLS cannot hide retained history from a schema owner's downgrade."""
    stage()
    migration = import_module(
        "parishkit.stewardship.accounts.migrations.0031_credential_installer_guards"
    )
    tables = (
        "stewardship_secret_request",
        "stewardship_sealed_credential_staging",
        "stewardship_credential_consumer_ack",
    )
    role = "review_owner_" + uuid4().hex
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f'CREATE ROLE "{role}" NOSUPERUSER NOBYPASSRLS NOINHERIT')
        cursor.execute(f'GRANT USAGE,CREATE ON SCHEMA public TO "{role}"')
        for table in tables:
            cursor.execute(f'ALTER TABLE "{table}" OWNER TO "{role}"')
        with (
            pytest.raises(IntegrityError, match="history prevents downgrade"),
            transaction.atomic(),
        ):
            cursor.execute(f'SET LOCAL ROLE "{role}"')
            cursor.execute(migration.Migration.operations[-1].reverse_sql)
        cursor.execute(
            "SELECT relforcerowsecurity FROM pg_class WHERE relname=ANY(%s)",
            [list(tables)],
        )
        assert cursor.fetchall() == [(True,), (True,), (True,)]
        # Roll back only this test's role, temporary ownership and schema grant.
        transaction.set_rollback(True)


def test_contended_health_probe_does_not_block_counter_admission(auth_service):
    """A busy process-local probe slot never queues ordinary counter scripts."""
    limiter = auth_service.limiter
    assert limiter.health_lock.acquire(blocking=False)
    try:
        assert limiter.bucket("admin", "192.0.2.1") == 0
    finally:
        limiter.health_lock.release()


def test_limiter_rejects_transaction_nesting_with_typed_error(auth_service):
    """An owning transaction cannot accidentally nest durable health commits."""
    with transaction.atomic(), pytest.raises(LimiterUnavailable, match="independent"):
        auth_service.limiter.bucket("admin", "192.0.2.1")


def test_database_health_failure_is_retryable_even_when_incident_store_fails(
    auth_service, monkeypatch
):
    """A broken SQL probe/audit store cannot leak exceptions through auth routes."""
    from parishkit.stewardship.accounts import limiter_health

    failure = Mock(side_effect=DatabaseError("synthetic private database failure"))
    monkeypatch.setattr(limiter_health, "observe_store", failure)
    monkeypatch.setattr(auth_service.limiter, "incident", failure)
    response = Client().get("/admin/login")
    assert response.status_code == 503 and response["Retry-After"] == "5"
    assert b"synthetic private" not in response.content


def test_admin_rejections_remain_bounded_beyond_rate_limit(auth_service):
    """Even pre-view throttled callbacks cannot allocate one permanent row each."""
    browser = Client()
    for _ in range(30):
        assert browser.get("/admin/oauth/callback").status_code in {403, 429}
    assert AuditEvent.objects.filter(event_type="admin_login_denied").count() == 1
