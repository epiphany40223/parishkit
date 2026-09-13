"""Internal secret-request storage and retryable external-staging cleanup.

These primitives are not authentication, encryption or credential installation.
Only authenticated Admin admission and target-isolated ARC-06 services may call
them. A target string is a consistency check, never a service identity.
The cleanup callback must remove only the named target-owned opaque object and
be idempotent when that object is already absent. That legacy cleanup port cannot
consume sealed requests: credential_installation owns their file/SQL protocol.
"""

import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS
from parishkit.stewardship.storage import (
    StaleRecordError,
    StorageInvariantError,
    UTCDateTimeField,
)

from .cryptography import TokenPublicKeyring, envelope_header
from .provider_context import validated_context
from .provider_models import ProviderValidationContext
from .secret_models import (
    MAX_STAGING_LIFETIME,
    SECRET_PENDING,
    SECRET_TARGETS,
    SealedCredentialStaging,
    SecretReplacementRequest,
)


class SecretRequestConflict(ConfigError):
    """A repeated intake identity carries different immutable request contents."""


@dataclass(frozen=True)
class SecretRequestStatus:
    """Safe receipt without staging location, secret bytes or credential values."""

    request_id: UUID
    state: str
    version: int
    cleanup_reason: str


def _identifiers(*values):
    """Reject caller-shaped identifiers without echoing potentially private text."""
    if any(not isinstance(value, UUID) for value in values):
        raise TypeError("Secret request identifiers must be UUIDs.")


def _target(value):
    """Keep target vocabulary closed until an explicit schema migration."""
    if type(value) is not str or value not in SECRET_TARGETS:
        raise ConfigError("Unknown secret request target.")


@contextmanager
def _transaction():
    """Own a durable short metadata transaction; never wrap external file work."""
    if (
        connection.vendor != "postgresql"
        or connection.in_atomic_block
        or not connection.get_autocommit()
    ):
        raise StorageInvariantError(
            "Secret requests require an independent PostgreSQL transaction."
        )
    with transaction.atomic(durable=True):
        # Low-volume administrative metadata: one lock avoids absent-row races
        # across both request IDs and target reservations, without lock ordering.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [736213, 1])
        yield


def _now():
    """Use the same database clock as SQL expiry guards, not a worker host clock."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT statement_timestamp()")
        return cursor.fetchone()[0]


def _receipt(record):
    """Copy state; the receipt is not proof of installed or tested credentials."""
    return SecretRequestStatus(
        record.pk, record.state, record.version, record.cleanup_reason
    )


def _get(identifier, **scope):
    """Make missing and out-of-scope identifiers indistinguishable."""
    record = SecretReplacementRequest.objects.filter(pk=identifier, **scope).first()
    if record is None:
        raise LookupError("Secret request is unavailable.")
    return record


def stage_secret_request(
    *,
    request_id,
    target,
    staging_reference,
    actor_id,
    reauthenticated_at,
    expires_at=None,
    staging_lifetime=None,
    expected_fingerprint,
    correlation_id,
    required_consumers=(),
    sealed_candidate=None,
    candidate_fingerprint=None,
    provider_settings=None,
    admit=None,
):
    """Record a trusted, already sealed staging reference with exact retry identity.

    ARC-04/ARC-06 own fresh authentication and may choose a shorter staging TTL;
    this storage layer enforces ordering and a hard 24-hour lifetime ceiling.
    An identical retry returns the original state, including after expiry/cleanup.
    A server-selected lifetime starts at first intake, never at form rendering;
    retries reuse that durable deadline rather than extending the reservation.
    """
    _identifiers(request_id, staging_reference, actor_id, correlation_id)
    _target(target)
    if provider_settings is not None:
        provider_settings = validated_context(target, provider_settings)
        if sealed_candidate is None:
            raise ConfigError("Provider validation requires sealed credential intake.")
    allowed_consumers = {
        role.value for role, names in ALLOWED_SECRETS.items() if target in names
    }
    if (
        type(required_consumers) is not tuple
        or any(type(value) is not str for value in required_consumers)
        or len(set(required_consumers)) != len(required_consumers)
        or (bool(required_consumers) and set(required_consumers) != allowed_consumers)
        or bool(required_consumers) != (sealed_candidate is not None)
        or (sealed_candidate is None) != (candidate_fingerprint is None)
    ):
        raise ConfigError("Invalid credential consumer/staging inventory.")
    if sealed_candidate is not None:
        if (
            type(sealed_candidate) is not str
            or len(sealed_candidate) > 262144
            or type(candidate_fingerprint) is not str
            or re.fullmatch(r"[0-9a-f]{64}", candidate_fingerprint) is None
        ):
            raise ConfigError("Invalid sealed credential payload.")
        envelope_header(sealed_candidate, TokenPublicKeyring.algorithm)
    field = UTCDateTimeField()
    if staging_lifetime is not None and (
        expires_at is not None
        or type(staging_lifetime) is not timedelta
        or not timedelta(0) < staging_lifetime <= MAX_STAGING_LIFETIME
    ):
        raise ConfigError("Invalid secret staging lifetime.")
    try:
        reauthenticated_at, expires_at = (
            field.to_python(reauthenticated_at),
            field.to_python(expires_at),
        )
    except ValidationError:
        raise ConfigError("Valid secret request timestamps are required.") from None
    if reauthenticated_at is None or (expires_at is None and staging_lifetime is None):
        raise ConfigError("Valid secret request timestamps are required.")
    if expected_fingerprint is not None and (
        type(expected_fingerprint) is not str
        or re.fullmatch(r"[0-9a-f]{64}", expected_fingerprint) is None
    ):
        raise ConfigError("Invalid expected credential fingerprint.")
    intent = dict(
        target=target,
        staging_reference=staging_reference,
        requested_by_id=actor_id,
        reauthenticated_at=reauthenticated_at,
        expires_at=expires_at,
        expected_fingerprint=expected_fingerprint,
        required_consumers=sorted(required_consumers),
    )
    with _transaction():
        if admit is not None and (not callable(admit) or admit() is not True):
            raise PermissionError("Secret request is not admitted.")
        existing = SecretReplacementRequest.objects.filter(pk=request_id).first()
        if staging_lifetime is not None:
            expires_at = (
                existing.expires_at
                if existing is not None
                else _now() + staging_lifetime
            )
            intent["expires_at"] = expires_at
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in intent.items()):
                raise SecretRequestConflict("Secret request identity is already bound.")
            context = ProviderValidationContext.objects.filter(request=existing).first()
            if (context.settings if context else None) != provider_settings:
                raise SecretRequestConflict(
                    "Secret request validation context is already bound."
                )
            if (
                required_consumers
                and not SealedCredentialStaging.objects.filter(
                    request=existing, fingerprint=candidate_fingerprint
                ).exists()
            ):
                raise SecretRequestConflict("Secret request identity is already bound.")
            return _receipt(existing)
        now = _now()
        if reauthenticated_at > now or expires_at <= now:
            raise ConfigError("Invalid secret request interval.")
        if expires_at - now > MAX_STAGING_LIFETIME:
            raise ConfigError("Secret staging lifetime cannot exceed 24 hours.")
        if SecretReplacementRequest.objects.filter(
            target=target, state__in=SECRET_PENDING
        ).exists():
            raise ConfigError("Credential target already has a pending request.")
        if sealed_candidate is not None:
            previous = (
                SecretReplacementRequest.objects.filter(target=target, state="applied")
                .order_by("-created_at", "-pk")
                .first()
            )
            if (
                previous is not None
                and previous.resulting_fingerprint != expected_fingerprint
            ):
                raise StaleRecordError(
                    "Select the installed credential before replacing it again."
                )
        if SecretReplacementRequest.objects.filter(
            staging_reference=staging_reference
        ).exists():
            raise ConfigError("Staging reference is already bound.")
        record = SecretReplacementRequest.objects.create(
            id=request_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
            **intent,
        )
        if required_consumers:
            SealedCredentialStaging.objects.create(
                reference=staging_reference,
                request=record,
                target=target,
                ciphertext=sealed_candidate,
                fingerprint=candidate_fingerprint,
            )
        if provider_settings is not None:
            ProviderValidationContext.objects.create(
                request=record,
                target=target,
                settings=provider_settings,
                actor_id=actor_id,
                correlation_id=correlation_id,
            )
        return _receipt(record)


def secret_request_status(*, request_id, actor_id):
    """Read only the requesting actor's receipt; callers still need current RBAC."""
    _identifiers(request_id, actor_id)
    return _receipt(_get(request_id, requested_by_id=actor_id))


def _transition(record, state, *, actor_id, correlation_id, reason=None):
    """Advance one locked state; SQL atomically owns timestamps, history and audit."""
    record.state = state
    record.version += 1
    record.actor_id = actor_id
    record.correlation_id = correlation_id
    if reason is not None:
        record.cleanup_reason = reason
    record.save()
    return _receipt(record)


def cancel_secret_request(*, request_id, actor_id, correlation_id):
    """Queue cleanup without claiming that staging is already gone."""
    _identifiers(request_id, actor_id, correlation_id)
    with _transaction():
        record = _get(request_id, requested_by_id=actor_id)
        if record.state != "staged":
            return _receipt(record)
        return _transition(
            record,
            "cleanup_pending",
            actor_id=actor_id,
            correlation_id=correlation_id,
            reason="cancelled",
        )


def expire_secret_request(*, request_id, target, correlation_id):
    """Queue due cleanup for the target service without a fabricated human actor."""
    _identifiers(request_id, correlation_id)
    _target(target)
    with _transaction():
        record = _get(request_id, target=target)
        if record.required_consumers:
            raise ConfigError("Sealed request expiry requires its target installer.")
        if record.state != "staged":
            return _receipt(record)
        if record.expires_at > _now():
            raise ConfigError("Secret request is not expired.")
        return _transition(
            record,
            "cleanup_pending",
            actor_id=None,
            correlation_id=correlation_id,
            reason="expired",
        )


def clean_secret_request(*, request_id, target, correlation_id, remove_payload):
    """Run an idempotent target-store deletion, then acknowledge it durably.

    Failure or a crash leaves the reservation in cleanup_pending. Concurrent retries
    may invoke deletion more than once, but terminal history/audit is exactly once.
    The callback must raise on failure, including uncertain deletion; it must never
    log secret material. This port supplies no file access or decryption itself.
    """
    _identifiers(request_id, correlation_id)
    _target(target)
    with _transaction():
        record = _get(request_id, target=target)
        if record.required_consumers:
            raise ConfigError(
                "Sealed installation cleanup requires its target installer."
            )
        if record.state in ("cancelled", "expired"):
            return _receipt(record)
        if record.state != "cleanup_pending":
            raise ConfigError("Secret request has not entered cleanup.")
        reference = record.staging_reference
    remove_payload(reference)
    with _transaction():
        record = _get(request_id, target=target)
        if record.state in ("cancelled", "expired"):
            return _receipt(record)
        return _transition(
            record, record.cleanup_reason, actor_id=None, correlation_id=correlation_id
        )
