"""Exercise credential queues with real non-superuser PostgreSQL identities."""

from contextlib import contextmanager
from datetime import timedelta
from time import monotonic, sleep
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection
from django.db.models import F
from django.utils import timezone

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.credential_database import admit_installer_database
from parishkit.stewardship.accounts.credential_files import CredentialFiles
from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.credential_installation import (
    CredentialInstaller,
    acknowledge_loaded_credential,
)
from parishkit.stewardship.accounts.cryptography import CryptographicError, Key
from parishkit.stewardship.accounts.key_files import (
    file_fingerprint,
    read_private,
    write_private,
)
from parishkit.stewardship.accounts.secret_models import (
    CredentialConsumerAcknowledgement,
    SealedCredentialStaging,
    SecretReplacementRequest,
)
from parishkit.stewardship.accounts.secret_requests import (
    cancel_secret_request,
    stage_secret_request,
)
from parishkit.stewardship.accounts.sessions import database_now

pytestmark = pytest.mark.django_db(transaction=True)
ROLES = (
    "pk_stewardship_web",
    "pk_stewardship_worker",
    "pk_stewardship_backup_worker",
    "pk_stewardship_credential_slack",
    "pk_stewardship_credential_parishsoft",
    "pk_stewardship_credential_metrics",
)


@pytest.fixture
def isolated_roles():
    """Own exact disposable roles; never adopt or alter an existing cluster role."""
    created = []
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", [list(ROLES)]
        )
        assert cursor.fetchall() == [], "Disposable test roles already exist."
    try:
        for role in ROLES:
            with connection.cursor() as cursor:
                cursor.execute(
                    f'CREATE ROLE "{role}" LOGIN NOSUPERUSER NOBYPASSRLS '
                    "NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT"
                )
                created.append(role)
                cursor.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
                cursor.execute(
                    "GRANT SELECT ON stewardship_secret_request, "
                    f'stewardship_credential_consumer_ack TO "{role}"'
                )
                cursor.execute(f'GRANT INSERT ON stewardship_audit_event TO "{role}"')
                cursor.execute(
                    "GRANT SELECT(active_configuration_id) ON "
                    f'stewardship_system_configuration TO "{role}"'
                )
                cursor.execute(
                    "GRANT SELECT(id, configuration_id) ON stewardship_parish "
                    f'TO "{role}"'
                )
                if role == "pk_stewardship_worker":
                    # SELECT FOR SHARE in the acknowledgement trigger needs one
                    # UPDATE privilege; RLS/immutable guards still deny mutation.
                    cursor.execute(
                        f'GRANT UPDATE(id) ON stewardship_secret_request TO "{role}"'
                    )
                    cursor.execute(
                        "GRANT INSERT ON stewardship_credential_consumer_ack "
                        f'TO "{role}"'
                    )
                elif role != "pk_stewardship_backup_worker":
                    cursor.execute(
                        f'GRANT UPDATE ON stewardship_secret_request TO "{role}"'
                    )
                    cursor.execute(
                        "GRANT SELECT, INSERT ON stewardship_secret_checkpoint "
                        f'TO "{role}"'
                    )
                if role == "pk_stewardship_web":
                    cursor.execute(
                        "GRANT INSERT ON stewardship_credential_consumer_ack "
                        f'TO "{role}"'
                    )
                    cursor.execute(
                        f'GRANT INSERT ON stewardship_secret_request TO "{role}"'
                    )
                    cursor.execute(
                        "GRANT INSERT, "
                        "SELECT(reference, request_id, target, fingerprint) "
                        f'ON stewardship_sealed_credential_staging TO "{role}"'
                    )
                elif role.startswith("pk_stewardship_credential_"):
                    cursor.execute(
                        "GRANT SELECT, UPDATE ON stewardship_sealed_credential_staging "
                        f'TO "{role}"'
                    )
                elif role == "pk_stewardship_backup_worker":
                    cursor.execute(
                        "GRANT SELECT ON stewardship_sealed_credential_staging "
                        f'TO "{role}"'
                    )
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")
            for role in reversed(created):
                cursor.execute(f'DROP OWNED BY "{role}"')
                cursor.execute(f'DROP ROLE "{role}"')


@contextmanager
def identity(role):
    """Set the actual authenticated SQL identity, not an application target hint."""
    assert role in ROLES
    with connection.cursor() as cursor:
        cursor.execute(f'SET SESSION AUTHORIZATION "{role}"')
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET SESSION AUTHORIZATION")


def stage(target="slack", *, expected_fingerprint="a" * 64, expires_at=None):
    """Seal synthetic data and exercise an identical randomized-envelope retry."""
    key = PrivateHandoff(target, Key("handoff", "active", b"h" * 32))
    identifier = uuid4()
    value = b"synthetic-candidate"
    intent = dict(
        request_id=identifier,
        target=target,
        staging_reference=uuid4(),
        actor_id=uuid4(),
        reauthenticated_at=timezone.now() - timedelta(seconds=30),
        expires_at=expires_at or timezone.now() + timedelta(minutes=5),
        expected_fingerprint=expected_fingerprint,
        correlation_id=uuid4(),
        required_consumers=("worker",),
        sealed_candidate=key.public().seal(identifier, value),
        candidate_fingerprint=file_fingerprint(value),
    )
    with identity("pk_stewardship_web"):
        receipt = stage_secret_request(**intent)
        assert (
            stage_secret_request(
                **{**intent, "sealed_candidate": key.public().seal(identifier, value)}
            )
            == receipt
        )
    return identifier, file_fingerprint(value)


def advance(identifier, state, **changes):
    """Use the same guarded versioned transition as an isolated installer."""
    return SecretReplacementRequest.objects.filter(pk=identifier).update(
        state=state,
        version=F("version") + 1,
        actor_id=None,
        correlation_id=uuid4(),
        **changes,
    )


def test_target_and_web_roles_cannot_cross_credential_or_campaign_boundaries(
    isolated_roles,
):
    """A real database role cannot impersonate another installer or read secrets."""
    slack, _ = stage()
    parishsoft, _ = stage("parishsoft")
    with identity("pk_stewardship_credential_slack"):
        assert not SealedCredentialStaging.objects.filter(
            request_id=parishsoft
        ).exists()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT ciphertext FROM stewardship_sealed_credential_staging "
                "WHERE request_id=%s",
                [parishsoft],
            )
            assert cursor.fetchall() == []
        assert list(SecretReplacementRequest.objects.values_list("pk", flat=True)) == [
            slack
        ]
        assert advance(parishsoft, "testing") == 0
        with pytest.raises(DatabaseError), connection.cursor() as cursor:
            cursor.execute("SELECT * FROM stewardship_family_campaign")
        with pytest.raises(DatabaseError), connection.cursor() as cursor:
            cursor.execute("SET ROLE pk_stewardship_credential_parishsoft")
    with identity("pk_stewardship_web"):
        assert (
            SealedCredentialStaging.objects.filter(request_id=slack)
            .values_list("fingerprint", flat=True)
            .get()
        )
        with pytest.raises(DatabaseError):
            SealedCredentialStaging.objects.get(request_id=slack)
        with pytest.raises(DatabaseError, match="Only the target installer"):
            advance(slack, "testing")
    with identity("pk_stewardship_backup_worker"):
        assert (
            SealedCredentialStaging.objects.filter(ciphertext__isnull=False).count()
            == 2
        )


def test_applied_requires_matching_consumer_identity_and_durable_scrubbing(
    isolated_roles,
):
    """Installed is not applied until the actual consumer attests and staging clears."""
    identifier, value_fingerprint = stage()
    with identity("pk_stewardship_credential_slack"):
        advance(identifier, "testing")
        advance(identifier, "installing", resulting_fingerprint=value_fingerprint)
        advance(identifier, "awaiting_ack")
        with pytest.raises(DatabaseError, match="not acknowledged"):
            advance(identifier, "cleanup_pending", cleanup_reason="applied")
    with identity("pk_stewardship_worker"):
        with pytest.raises(DatabaseError, match="not authorized"):
            CredentialConsumerAcknowledgement.objects.create(
                request_id=identifier,
                consumer="worker",
                fingerprint="b" * 64,
                correlation_id=uuid4(),
            )
        CredentialConsumerAcknowledgement.objects.create(
            request_id=identifier,
            consumer="worker",
            fingerprint=value_fingerprint,
            correlation_id=uuid4(),
        )
    with identity("pk_stewardship_credential_slack"):
        advance(identifier, "cleanup_pending", cleanup_reason="applied")
        with pytest.raises(DatabaseError, match="must be scrubbed"):
            advance(identifier, "applied")
        SealedCredentialStaging.objects.filter(request_id=identifier).update(
            ciphertext=None
        )
        advance(identifier, "applied")
        record = SecretReplacementRequest.objects.get(pk=identifier)
        assert record.installed_at <= record.acknowledged_at <= record.scrubbed_at


@pytest.fixture
def installer(tmp_path, isolated_roles):
    """Combine actual private files with actual restricted database identities."""
    directory = tmp_path.resolve() / "slack"
    directory.mkdir(mode=0o700)
    path = directory / "credential"
    write_private(path, b"synthetic-prior")
    files = CredentialFiles(
        path, PrivateHandoff("slack", Key("handoff", "active", b"h" * 32))
    )
    return CredentialInstaller(files, validate=lambda value: True)


def stage_for(installer, **changes):
    """Bind a request to its actual previous working-file fingerprint."""
    return stage(
        expected_fingerprint=file_fingerprint(read_private(installer.files.path)),
        **changes,
    )[0]


def run(installer):
    """Production-like queue calls always execute as the exact target identity."""
    with identity("pk_stewardship_credential_slack"):
        return installer.run_once()


def acknowledge(identifier):
    """A consumer attests the bytes it loaded; duplicate startup is idempotent."""
    with identity("pk_stewardship_worker"):
        for _ in range(2):
            acknowledge_loaded_credential(
                request_id=identifier,
                consumer="worker",
                loaded_value=b"synthetic-candidate",
            )


def test_installer_rejects_superuser_and_unexpected_grants(installer):
    """Target hints, superusers and unexpected campaign read grants are denied."""
    with pytest.raises(ConfigError, match="identity is not isolated"):
        installer.run_once()
    with identity("pk_stewardship_credential_slack"):
        admit_installer_database("slack")
        with pytest.raises(ConfigError, match="identity is not isolated"):
            admit_installer_database("parishsoft")
    with connection.cursor() as cursor:
        cursor.execute(
            "GRANT SELECT ON stewardship_family_campaign "
            "TO pk_stewardship_credential_slack"
        )
    with pytest.raises(ConfigError, match="grants are excessive"):
        run(installer)


def test_queue_file_roundtrip_waits_for_consumer_and_scrubs_both_stores(installer):
    """Installed state survives retries, but cannot claim applied without an ACK."""
    identifier = stage_for(installer)
    assert run(installer).state == "awaiting_ack"
    assert read_private(installer.files.path) == b"synthetic-candidate"
    assert SealedCredentialStaging.objects.get(request_id=identifier).ciphertext is None
    assert run(installer).state == "awaiting_ack"
    acknowledge(identifier)
    assert run(installer).state == "applied"
    assert not installer.files.journal_path.exists()
    assert run(installer) is None


@pytest.mark.parametrize("failure", [False, "exception"])
def test_failed_candidate_test_preserves_prior_and_scrubs_intake(installer, failure):
    """Private provider errors become only a failed receipt, never persisted detail."""
    identifier = stage_for(installer)

    def validate(value):
        """Simulate a provider reflecting a supplied private value."""
        if failure == "exception":
            raise ValueError(value.decode())
        return False

    installer.validate = validate
    assert run(installer).state == "failed"
    assert read_private(installer.files.path) == b"synthetic-prior"
    assert SealedCredentialStaging.objects.get(request_id=identifier).ciphertext is None
    assert not installer.files.journal_path.exists()


def test_cancelled_request_is_never_tested_or_installed(installer):
    """Cancellation reserves its target until the installer durably cleans staging."""
    identifier = stage_for(installer)
    actor = SecretReplacementRequest.objects.get(pk=identifier).requested_by_id
    with identity("pk_stewardship_web"):
        cancel_secret_request(
            request_id=identifier, actor_id=actor, correlation_id=uuid4()
        )
    assert run(installer).state == "cancelled"
    assert read_private(installer.files.path) == b"synthetic-prior"


def test_installed_request_expiry_restores_prior_before_terminal_receipt(installer):
    """A missing consumer never strands the new working credential after expiry."""
    deadline = database_now() + timedelta(seconds=20)
    stage_for(installer, expires_at=deadline)
    assert run(installer).state == "awaiting_ack"
    wait_for_database_deadline(deadline)
    assert run(installer).state == "expired"
    assert read_private(installer.files.path) == b"synthetic-prior"
    assert not installer.files.journal_path.exists()


def test_crash_after_rename_reconciles_without_retesting_or_double_history(
    installer, monkeypatch
):
    """A filesystem exception can follow a committed rename; retry reads evidence."""
    from parishkit.stewardship.accounts import credential_files

    identifier = stage_for(installer)

    def rename_then_crash(path, value, **kwargs):
        """Only the working-file write fails after its durable side effect."""
        write_private(path, value, **kwargs)
        if path == installer.files.path:
            raise CryptographicError("Synthetic interruption after rename.")

    with monkeypatch.context() as patch:
        patch.setattr(credential_files, "write_private", rename_then_crash)
        with pytest.raises(CryptographicError, match="after rename"):
            run(installer)
    assert read_private(installer.files.path) == b"synthetic-candidate"
    assert run(installer).state == "awaiting_ack"
    acknowledge(identifier)
    assert run(installer).state == "applied"
    assert list(
        SecretReplacementRequest.objects.get(pk=identifier)
        .checkpoints.order_by("sequence")
        .values_list("state", flat=True)
    ) == [
        "staged",
        "testing",
        "installing",
        "awaiting_ack",
        "cleanup_pending",
        "applied",
    ]


def test_crash_after_ack_decision_completes_even_after_deadline(installer, monkeypatch):
    """Committed consumer approval cannot turn into a rollback on a delayed retry."""
    deadline = database_now() + timedelta(seconds=20)
    identifier = stage_for(installer, expires_at=deadline)
    assert run(installer).state == "awaiting_ack"
    acknowledge(identifier)

    def crash(row):
        """Stop after the database approval but before journal scrubbing."""
        raise CryptographicError("Synthetic interruption after acknowledgement.")

    with monkeypatch.context() as patch:
        patch.setattr(installer, "_cleanup", crash)
        with pytest.raises(CryptographicError, match="after acknowledgement"):
            run(installer)
    wait_for_database_deadline(deadline)
    assert run(installer).state == "applied"
    assert read_private(installer.files.path) == b"synthetic-candidate"


def wait_for_database_deadline(deadline):
    """Wait on the authoritative clock, with a bounded host-side test deadline."""
    stop = monotonic() + 25
    while database_now() <= deadline:
        if monotonic() >= stop:
            pytest.fail("Disposable database clock did not reach the test deadline")
        sleep(0.1)
