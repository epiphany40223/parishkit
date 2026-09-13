"""Immutable provider scope over real web and target-specific PostgreSQL logins."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.credential_errors import (
    CredentialValidationUnavailable,
)
from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.cryptography import Key
from parishkit.stewardship.accounts.key_files import file_fingerprint
from parishkit.stewardship.accounts.provider_models import ProviderValidationContext
from parishkit.stewardship.accounts.secret_models import SecretReplacementRequest
from parishkit.stewardship.accounts.secret_requests import stage_secret_request
from parishkit.stewardship.provider_checks import request_validator

from .test_credential_isolation_postgresql import (  # noqa: F401
    advance,
    identity,
    installer,
    isolated_roles,
    run,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("isolated_roles"),
]


def intent(*, target="slack", expected_fingerprint=None):
    """Use unrelated synthetic secret bytes and canonical non-secret settings."""
    identifier = uuid4()
    private = PrivateHandoff(target, Key("handoff", "active", b"h" * 32))
    return dict(
        request_id=identifier,
        target=target,
        staging_reference=uuid4(),
        actor_id=uuid4(),
        reauthenticated_at=timezone.now() - timedelta(seconds=20),
        expires_at=timezone.now() + timedelta(minutes=5),
        expected_fingerprint=expected_fingerprint,
        correlation_id=uuid4(),
        required_consumers=("worker",),
        sealed_candidate=private.public().seal(identifier, b"synthetic-candidate"),
        candidate_fingerprint=file_fingerprint(b"synthetic-candidate"),
        provider_settings=(
            {"channel_id": "C123456789"}
            if target == "slack"
            else {"organization_id": 123}
        ),
    )


def test_context_exact_retry_and_target_isolation():
    """Retries cannot add, omit or change scope; other installers cannot read it."""
    arguments = intent()
    with identity("pk_stewardship_web"):
        first = stage_secret_request(**arguments)
        assert stage_secret_request(**arguments) == first
        for value in (None, {"channel_id": "C987654321"}):
            with pytest.raises(ConfigError, match="already bound"):
                stage_secret_request(**(arguments | {"provider_settings": value}))
        assert (
            ProviderValidationContext.objects.get().settings
            == arguments["provider_settings"]
        )
    with identity("pk_stewardship_credential_slack"):
        assert ProviderValidationContext.objects.get().request_id == first.request_id
    with identity("pk_stewardship_credential_parishsoft"):
        assert not ProviderValidationContext.objects.exists()
    with identity("pk_stewardship_worker"), pytest.raises(DatabaseError):
        ProviderValidationContext.objects.exists()


def test_provider_context_forces_owner_policies():
    """The migration must not exempt the table owner from target intake policy."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE oid='public.stewardship_provider_context'::regclass"
        )
        assert cursor.fetchone() == (True, True)


def test_context_cannot_be_attached_later_or_modified():
    """Even direct SQL cannot attach new scope to a previously committed intake."""
    arguments = intent()
    with identity("pk_stewardship_web"):
        first = stage_secret_request(**(arguments | {"provider_settings": None}))
        with pytest.raises(ConfigError, match="already bound"):
            stage_secret_request(**arguments)
        with (
            pytest.raises(DatabaseError, match="original intake"),
            transaction.atomic(),
        ):
            ProviderValidationContext.objects.create(
                request_id=first.request_id,
                target="slack",
                settings=arguments["provider_settings"],
                actor_id=arguments["actor_id"],
            )
    assert not ProviderValidationContext.objects.exists()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE stewardship_provider_context SET settings='{}'::jsonb",
        "DELETE FROM stewardship_provider_context",
    ],
)
def test_context_sql_history_is_immutable(statement):
    """Schema-owner SQL tests the guard, rather than relying on ORM immutability."""
    with identity("pk_stewardship_web"):
        stage_secret_request(**intent())
    with (
        pytest.raises(DatabaseError),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement)


@pytest.mark.parametrize(
    "settings",
    [
        {"channel_id": "https://example.org"},
        {"channel_id": "C123", "token": "forbidden"},
        {"organization_id": 123},
        [],
    ],
)
def test_sql_rejects_malformed_scope_at_intake(monkeypatch, settings):
    """Bypass Python validation to verify failure rolls back the whole reservation."""
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.secret_requests.validated_context",
        lambda target, values: values,
    )
    with identity("pk_stewardship_web"), pytest.raises(DatabaseError):
        stage_secret_request(**(intent() | {"provider_settings": settings}))
    assert not SecretReplacementRequest.objects.exists()
    assert not ProviderValidationContext.objects.exists()


def test_installer_receives_exact_request_and_candidate(request):
    """A context-aware validator replaces the legacy callback, never both."""
    service = request.getfixturevalue("installer")
    arguments = intent(expected_fingerprint=file_fingerprint(b"synthetic-prior"))
    with identity("pk_stewardship_web"):
        stage_secret_request(**arguments)
    calls = []

    def validate(identifier, value):
        """Read as the isolated target; no complete application configuration grant."""
        context = ProviderValidationContext.objects.get(request_id=identifier)
        calls.append((identifier, value, context.settings))
        return True

    def legacy(value):
        raise AssertionError("Legacy validator must not run.")

    service.validate = legacy
    service.validate_request = validate
    assert run(service).state == "awaiting_ack"
    assert calls == [
        (
            arguments["request_id"],
            b"synthetic-candidate",
            arguments["provider_settings"],
        )
    ]


def test_request_validator_closes_sql_before_provider_io(monkeypatch):
    """Use the target's actual SELECT grants, then prove the private helper order."""
    arguments = intent()
    with identity("pk_stewardship_web"):
        stage_secret_request(**arguments)
    events = []
    # This fixture uses SET SESSION AUTHORIZATION on the disposable owner
    # connection. Closing it would destroy the fixture identity, unlike an actual
    # runtime connection configured with the isolated target login. Record the
    # close call here; runtime tests independently exercise real reconnections.
    monkeypatch.setattr(
        "django.db.connections.close_all", lambda: events.append("close")
    )

    def external(target, settings, value, *, seconds, check):
        """No provider call before SQL close or beyond the request's deadline."""
        assert events == ["check", "close"]
        assert (target, settings, value) == (
            "slack",
            arguments["provider_settings"],
            b"synthetic-candidate",
        )
        assert 0 < seconds <= 30
        return True

    monkeypatch.setattr(
        "parishkit.stewardship.provider_checks.check_candidate", external
    )
    validator = request_validator("slack", check=lambda: events.append("check"))
    with identity("pk_stewardship_credential_slack"):
        advance(arguments["request_id"], "testing")
        assert validator(arguments["request_id"], b"synthetic-candidate") is True


@pytest.mark.parametrize("scenario", ["missing", "unclaimed", "foreign", "expiring"])
def test_request_validator_denies_unavailable_scope(monkeypatch, scenario):
    """Missing scope is never replaced with a guessed channel, tenant or mailbox."""
    arguments = intent(target="parishsoft" if scenario == "foreign" else "slack")
    if scenario == "missing":
        arguments["provider_settings"] = None
    if scenario == "expiring":
        arguments["expires_at"] = timezone.now() + timedelta(seconds=4)
    with identity("pk_stewardship_web"):
        stage_secret_request(**arguments)
    calls = []
    monkeypatch.setattr(
        "django.db.connections.close_all", lambda: calls.append("close")
    )
    monkeypatch.setattr(
        "parishkit.stewardship.provider_checks.check_candidate",
        lambda *args, **kwargs: calls.append("provider"),
    )
    with identity("pk_stewardship_credential_" + arguments["target"]):
        if scenario != "unclaimed":
            advance(arguments["request_id"], "testing")
    with (
        identity("pk_stewardship_credential_slack"),
        pytest.raises(CredentialValidationUnavailable),
    ):
        request_validator("slack", check=lambda: None)(
            arguments["request_id"], b"synthetic"
        )
    assert calls == ["close"]
