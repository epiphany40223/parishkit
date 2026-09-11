"""Target-isolated queue/file reconciliation; production launch belongs to OPS-04.

The database is the queue, not a broker carrying credential-bearing task bodies.
Every short transaction rechecks the actual SQL login and narrow grants. Target
flock spans separate commits and file renames, including database reconnects.
Provider-specific tests and whole-consumer recreation are supplied by the owning
runtime/provider integrations; this module never controls the Docker socket.
"""

from functools import partial
from uuid import UUID, uuid4

from django.db import transaction

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.service_boundaries import (
    ALLOWED_SECRETS,
    admit_online_service,
)

from .credential_database import admit_consumer_database, admit_installer_database
from .credential_files import CredentialFiles
from .credential_handoff import PrivateHandoff
from .cryptography import CryptographicError
from .key_files import file_fingerprint, load_keyring
from .metrics_credentials import credential_receipt
from .secret_models import (
    SECRET_PENDING,
    CredentialConsumerAcknowledgement,
    SealedCredentialStaging,
    SecretReplacementRequest,
)
from .secret_requests import _now, _receipt, _transaction, _transition


class CredentialValidationUnavailable(Exception):
    """A provider adapter requests retry without exposing provider error text."""

    def __init__(self):
        super().__init__("Credential validation is temporarily unavailable.")


class CredentialInstaller:
    """Internal orchestrator: typed files alone do not authorize queue access.

    Operational entry points must call ``from_configuration``; constructing this
    object directly remains useful for real-database/private-file integration
    tests, but still cannot bypass actual database identity and grant checks.
    """

    def __init__(self, files, *, validate):
        if not isinstance(files, CredentialFiles) or not callable(validate):
            raise TypeError("Credential files and target validation are required.")
        self.files, self.validate = files, validate
        self.target = files.private.target

    @classmethod
    def from_configuration(cls, configuration, *, validate):
        """Admit actual mounts and SQL identity before loading the one private key."""
        if admit_online_service(configuration) is not ServiceRole.CREDENTIAL_INSTALLER:
            raise ConfigError("A target-specific credential installer is required.")
        admit_installer_database(configuration.credential_target)
        ring = load_keyring(configuration.secrets["handoff_private"], "token_private")
        if len(ring.keys) != 1:
            raise ConfigError("Credential handoff requires one target-specific key.")
        return cls(
            CredentialFiles(
                configuration.secrets[configuration.credential_target],
                PrivateHandoff(configuration.credential_target, ring.active),
            ),
            validate=validate,
        )

    def _read(self, identifier):
        """Use RLS and explicit target binding, never a caller-provided target claim."""
        admit_installer_database(self.target)
        row = SecretReplacementRequest.objects.filter(
            pk=identifier, target=self.target
        ).first()
        if row is None or not row.required_consumers:
            raise ConfigError("Credential request is unavailable.")
        return row

    def _advance(self, identifier, old_state, state, *, reason=None, fingerprint=None):
        """A checkpoint and its redacted audit commit together, never around file IO."""
        with _transaction():
            row = self._read(identifier)
            if row.state != old_state:
                return row
            if fingerprint is not None:
                row.resulting_fingerprint = fingerprint
            _transition(
                row, state, actor_id=None, correlation_id=uuid4(), reason=reason
            )
            if state == "awaiting_ack":
                SealedCredentialStaging.objects.filter(
                    request_id=identifier, ciphertext__isnull=False
                ).update(ciphertext=None)
            # Trigger-owned timestamps are needed for deadline-safe replay.
            row.refresh_from_db()
            return row

    def _candidate(self, row):
        """Test only the exact sealed intake, with no private error text escaping."""
        staged = SealedCredentialStaging.objects.get(request_id=row.pk)
        valid = False
        try:
            value = self.files.private.open(row.pk, staged.ciphertext)
            value.decode("utf-8")
            valid = (
                credential_receipt(value, self.target) == staged.fingerprint
                and self.validate(value) is True
            )
            if valid and self.target == "metrics":
                from django.db.models import Q

                valid = staged.fingerprint != row.expected_fingerprint and not (
                    SecretReplacementRequest.objects.filter(target="metrics")
                    .exclude(pk=row.pk)
                    .filter(
                        Q(expected_fingerprint=staged.fingerprint)
                        | Q(resulting_fingerprint=staged.fingerprint)
                    )
                    .exists()
                )
        except CredentialValidationUnavailable:
            # Retain testing state and sealed input; the owning loop retries
            # with its bounded backoff until the request's ordinary expiry.
            raise CredentialValidationUnavailable() from None
        except Exception:
            # Provider exceptions can contain the supplied credential. The public
            # outcome is only failed, and the previous file has not been changed.
            pass
        if not valid:
            return self._advance(row.pk, "testing", "cleanup_pending", reason="failed")
        self.files.prepare(
            request_id=row.pk,
            candidate=staged.ciphertext,
            expected_fingerprint=self._private_predecessor(row),
            consumers=tuple(row.required_consumers),
            expires_at=row.expires_at,
            now=_now(),
            validate=lambda value: True,
        )
        return self._advance(
            row.pk, "testing", "installing", fingerprint=staged.fingerprint
        )

    def _private_predecessor(self, row):
        """Keep metrics byte hashes in the isolated file journal, never PostgreSQL."""
        selected = self.files._selected()
        public = (
            credential_receipt(selected, self.target) if selected is not None else None
        )
        if public != row.expected_fingerprint:
            raise CryptographicError("Working credential identity has changed.")
        return file_fingerprint(selected) if selected is not None else None

    def _acknowledged(self, row):
        """Commit the all-consumer decision before discarding rollback material.

        Its SQL-assigned acknowledged_at is the replay clock: a crash after that
        durable decision must finish it, not undo an acknowledged replacement just
        because the process resumes after the original staging deadline.
        """
        with _transaction():
            current = self._read(row.pk)
            if current.state != "awaiting_ack":
                return current
            if current.expires_at <= _now():
                reason = "expired"
            else:
                acknowledgements = dict(
                    CredentialConsumerAcknowledgement.objects.filter(
                        request_id=row.pk
                    ).values_list("consumer", "fingerprint")
                )
                if any(
                    acknowledgements.get(name) != current.resulting_fingerprint
                    for name in current.required_consumers
                ):
                    return current
                reason = "applied"
            _transition(
                current,
                "cleanup_pending",
                actor_id=None,
                correlation_id=uuid4(),
                reason=reason,
            )
            current.refresh_from_db()
            return current

    def _terminal(self, identifier, file_receipt=None):
        """Scrub staging and commit terminal history before releasing the journal."""
        with _transaction():
            row = self._read(identifier)
            if file_receipt is not None:
                expected_state = (
                    "applied" if row.cleanup_reason == "applied" else "rolled_back"
                )
                expected = (
                    row.resulting_fingerprint
                    if row.cleanup_reason == "applied"
                    else row.expected_fingerprint
                )
                if self.target == "metrics":
                    selected = self.files._selected()
                    public = (
                        credential_receipt(selected, self.target)
                        if selected is not None
                        else None
                    )
                    if public != expected:
                        raise CryptographicError(
                            "Credential terminal identity disagrees."
                        )
                    expected = (
                        file_fingerprint(selected) if selected is not None else None
                    )
                if (
                    file_receipt.request_id != identifier
                    or file_receipt.state != expected_state
                    or file_receipt.fingerprint != expected
                ):
                    raise CryptographicError("Credential terminal evidence disagrees.")
            if row.state == "cleanup_pending":
                SealedCredentialStaging.objects.filter(
                    request_id=identifier, ciphertext__isnull=False
                ).update(ciphertext=None)
                _transition(
                    row, row.cleanup_reason, actor_id=None, correlation_id=uuid4()
                )
            elif row.state in SECRET_PENDING:
                raise ConfigError("Credential request has no terminal decision.")
        return True

    def _cleanup(self, row):
        """Failure/expiry restores the prior file before recording completion."""
        if self.files.pending_request() is not None:
            if row.cleanup_reason == "applied":
                acknowledgements = dict(
                    CredentialConsumerAcknowledgement.objects.filter(
                        request_id=row.pk
                    ).values_list("consumer", "fingerprint")
                )
                if self.target == "metrics":
                    if any(
                        acknowledgements.get(name) != row.resulting_fingerprint
                        for name in row.required_consumers
                    ):
                        raise CryptographicError("Metrics consumer identity disagrees.")
                    # The durable decision already validated public receipts.
                    # The file protocol independently verifies the exact loaded
                    # bytes before destroying its sealed rollback material.
                    digest = self.files._read(row.pk)["candidate_fingerprint"]
                    acknowledgements = {name: digest for name in row.required_consumers}
                self.files.acknowledge(
                    row.pk,
                    fingerprints=acknowledgements,
                    now=row.acknowledged_at,
                )
            else:
                self.files.rollback(row.pk)
            self.files.release(
                row.pk,
                record_terminal=lambda receipt: self._terminal(row.pk, receipt),
            )
        else:
            if row.cleanup_reason == "applied":
                raise CryptographicError("Applied credential file evidence is missing.")
            selected = self.files._selected()
            actual = (
                credential_receipt(selected, self.target)
                if selected is not None
                else None
            )
            if actual != row.expected_fingerprint:
                raise CryptographicError("Working credential fingerprint has changed.")
            self._terminal(row.pk)
        return self._read(row.pk)

    def run_once(self):
        """Bound one queue pass; missing acknowledgements yield rather than sleep."""
        admit_installer_database(self.target)
        with self.files.lock():
            identifier = self.files.pending_request()
            if identifier is None:
                identifier = (
                    SecretReplacementRequest.objects.filter(
                        target=self.target, state__in=SECRET_PENDING
                    )
                    .exclude(required_consumers=[])
                    .order_by("created_at", "pk")
                    .values_list("pk", flat=True)
                    .first()
                )
            if identifier is None:
                return None
            for _ in range(6):
                row = self._read(identifier)
                if row.state not in SECRET_PENDING:
                    if self.files.pending_request() is not None:
                        self.files.release(
                            row.pk,
                            record_terminal=partial(self._terminal, row.pk),
                        )
                    return _receipt(row)
                if row.state != "cleanup_pending" and row.expires_at <= _now():
                    row = self._advance(
                        row.pk, row.state, "cleanup_pending", reason="expired"
                    )
                if row.state == "staged":
                    self._advance(row.pk, "staged", "testing")
                elif row.state == "testing":
                    self._candidate(row)
                elif row.state == "installing":
                    installed = self.files.install(row.pk, now=_now())
                    if installed.state == "rolled_back":
                        self._advance(
                            row.pk, "installing", "cleanup_pending", reason="expired"
                        )
                    else:
                        self._advance(row.pk, "installing", "awaiting_ack")
                elif row.state == "awaiting_ack":
                    if self._acknowledged(row).state == "awaiting_ack":
                        return _receipt(row)
                elif row.state == "cleanup_pending":
                    return _receipt(self._cleanup(row))
            raise ConfigError("Credential queue exceeded its transition bound.")


def acknowledge_loaded_credential(*, request_id, consumer, loaded_value):
    """Internal consumer-startup hook after the process has loaded its credential.

    Each named consumer denotes the entire single-instance Compose service. Its
    owning supervisor invokes this only after all child processes have loaded the
    replacement. A per-file bind mount retains the old inode until recreation;
    reading an installer path or signaling an old container is not acknowledgement.
    No browser endpoint accepts these arguments as consumer authority.
    """
    if not isinstance(request_id, UUID):
        raise TypeError("A credential request UUID is required.")
    admit_consumer_database(consumer)
    with transaction.atomic(durable=True):
        row = SecretReplacementRequest.objects.get(pk=request_id)
        if row.target not in ALLOWED_SECRETS[ServiceRole(consumer)]:
            raise ConfigError("Credential consumer target is not authorized.")
        fingerprint = credential_receipt(loaded_value, row.target)
        existing = CredentialConsumerAcknowledgement.objects.filter(
            request_id=request_id, consumer=consumer
        ).first()
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise ConfigError(
                    "Credential acknowledgement identity is already bound."
                )
            return
        CredentialConsumerAcknowledgement.objects.create(
            request_id=request_id,
            consumer=consumer,
            fingerprint=fingerprint,
            correlation_id=uuid4(),
        )
