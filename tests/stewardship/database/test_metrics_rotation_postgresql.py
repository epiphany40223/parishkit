"""Metrics rotates through sealed files and public receipts without database hashes."""

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from parishkit.stewardship.accounts.credential_files import CredentialFiles
from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.credential_installation import (
    CredentialInstaller,
    acknowledge_loaded_credential,
)
from parishkit.stewardship.accounts.cryptography import CryptographicError, Key
from parishkit.stewardship.accounts.key_files import read_private, write_private
from parishkit.stewardship.accounts.metrics_credentials import MetricsCredential
from parishkit.stewardship.accounts.secret_models import (
    CredentialConsumerAcknowledgement,
    SealedCredentialStaging,
    SecretReplacementRequest,
    SecretRequestCheckpoint,
)
from parishkit.stewardship.accounts.secret_requests import stage_secret_request
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditEvent

from .test_credential_isolation_postgresql import identity, wait_for_database_deadline
from .test_credential_isolation_postgresql import isolated_roles as isolated_roles

pytestmark = pytest.mark.django_db(transaction=True)


def stage_metrics(private, prior, candidate, *, expires_at=None):
    """Intake carries the independent version label, never a token/file hash."""
    identifier = uuid4()
    with identity("pk_stewardship_web"):
        stage_secret_request(
            request_id=identifier,
            target="metrics",
            staging_reference=uuid4(),
            actor_id=uuid4(),
            reauthenticated_at=timezone.now() - timedelta(seconds=1),
            expires_at=expires_at or database_now() + timedelta(minutes=5),
            expected_fingerprint=prior.receipt,
            correlation_id=uuid4(),
            required_consumers=("web",),
            sealed_candidate=private.public().seal(identifier, candidate.serialize()),
            candidate_fingerprint=candidate.receipt,
        )
    return identifier


def run_metrics(installer):
    """The actual target login admits every state transition and file operation."""
    with identity("pk_stewardship_credential_metrics"):
        return installer.run_once()


@pytest.fixture
def metric_installer(tmp_path, isolated_roles):
    """Real owner-only files and real database identities, not provider mocks."""
    prior, candidate = MetricsCredential.generate(), MetricsCredential.generate()
    directory = tmp_path / "metrics"
    directory.mkdir(mode=0o700)
    path = directory / "credential"
    write_private(path, prior.serialize())
    private = PrivateHandoff("metrics", Key("handoff", "active", b"h" * 32))

    def validate(value):
        """Metrics has a local schema test and never contacts an external service."""
        MetricsCredential.parse(value)
        return True

    installer = CredentialInstaller(CredentialFiles(path, private), validate=validate)
    return installer, private, prior, candidate


def test_metrics_rotation_stores_no_token_or_token_hash(metric_installer):
    installer, private, prior, candidate = metric_installer
    identifier = stage_metrics(private, prior, candidate)
    assert run_metrics(installer).state == "awaiting_ack"
    assert read_private(installer.files.path) == candidate.serialize()
    with identity("pk_stewardship_web"):
        acknowledge_loaded_credential(
            request_id=identifier, consumer="web", loaded_value=candidate.serialize()
        )
    assert run_metrics(installer).state == "applied"
    assert not installer.files.journal_path.exists()
    assert (
        SecretReplacementRequest.objects.get(pk=identifier).resulting_fingerprint
        == candidate.receipt
    )
    rows = [
        list(model.objects.values())
        for model in (
            SecretReplacementRequest,
            SecretRequestCheckpoint,
            SealedCredentialStaging,
            CredentialConsumerAcknowledgement,
            AuditEvent,
        )
    ]
    serialized = json.dumps(rows, default=str)
    for credential in (prior, candidate):
        assert credential.token.decode() not in serialized
        assert hashlib.sha256(credential.token).hexdigest() not in serialized
        assert hashlib.sha256(credential.serialize()).hexdigest() not in serialized


def test_metrics_rotation_rejects_reusing_current_receipt(metric_installer):
    installer, private, prior, candidate = metric_installer
    candidate = MetricsCredential(prior.receipt, candidate.token)
    identifier = stage_metrics(private, prior, candidate)
    assert run_metrics(installer).state == "failed"
    assert read_private(installer.files.path) == prior.serialize()
    assert (
        SecretReplacementRequest.objects.get(pk=identifier).resulting_fingerprint
        is None
    )


def test_metrics_expiry_restores_prior_without_persisting_its_hash(metric_installer):
    installer, private, prior, candidate = metric_installer
    deadline = database_now() + timedelta(seconds=20)
    stage_metrics(private, prior, candidate, expires_at=deadline)
    assert run_metrics(installer).state == "awaiting_ack"
    wait_for_database_deadline(deadline)
    assert run_metrics(installer).state == "expired"
    assert read_private(installer.files.path) == prior.serialize()
    assert not installer.files.journal_path.exists()


def test_public_receipt_does_not_replace_private_byte_integrity(metric_installer):
    """An altered file with the same public version cannot retire rollback data."""
    installer, private, prior, candidate = metric_installer
    identifier = stage_metrics(private, prior, candidate)
    assert run_metrics(installer).state == "awaiting_ack"
    changed = MetricsCredential(candidate.receipt, MetricsCredential.generate().token)
    write_private(installer.files.path, changed.serialize())
    with identity("pk_stewardship_web"):
        acknowledge_loaded_credential(
            request_id=identifier, consumer="web", loaded_value=changed.serialize()
        )
    with pytest.raises(CryptographicError, match="unexpected fingerprint"):
        run_metrics(installer)
    assert installer.files.journal_path.exists()
    assert read_private(installer.files.path) == changed.serialize()
    assert (
        SecretReplacementRequest.objects.get(pk=identifier).state == "cleanup_pending"
    )
    # The fixture restores the exact known candidate, then ordinary replay finishes.
    write_private(installer.files.path, candidate.serialize())
    assert run_metrics(installer).state == "applied"


def test_metrics_receipt_cannot_be_reused_from_earlier_history(metric_installer):
    """A version from before the current predecessor cannot acknowledge new bytes."""
    installer, private, prior, candidate = metric_installer
    identifier = stage_metrics(private, prior, candidate)
    assert run_metrics(installer).state == "awaiting_ack"
    with identity("pk_stewardship_web"):
        acknowledge_loaded_credential(
            request_id=identifier, consumer="web", loaded_value=candidate.serialize()
        )
    assert run_metrics(installer).state == "applied"
    reused = MetricsCredential(prior.receipt, MetricsCredential.generate().token)
    stage_metrics(private, candidate, reused)
    assert run_metrics(installer).state == "failed"
    assert read_private(installer.files.path) == candidate.serialize()


def test_metrics_reuse_query_failure_cannot_install_candidate(
    metric_installer, monkeypatch
):
    """A transient database failure after syntax validation must remain fail-closed."""
    from django.db.models.query import QuerySet

    installer, private, prior, candidate = metric_installer
    stage_metrics(private, prior, candidate)
    original = QuerySet.exists
    calls = []

    def fail_reuse_query(queryset):
        """Only the reuse lookup fails; later checkpoint persistence can recover."""
        if (
            queryset.model is SecretReplacementRequest
            and "resulting_fingerprint" in str(queryset.query)
        ):
            calls.append(True)
            raise RuntimeError("synthetic database outage")
        return original(queryset)

    monkeypatch.setattr(QuerySet, "exists", fail_reuse_query)
    assert run_metrics(installer).state == "failed"
    assert calls == [True]
    assert read_private(installer.files.path) == prior.serialize()
