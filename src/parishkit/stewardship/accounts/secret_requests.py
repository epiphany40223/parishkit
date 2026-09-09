"""Internal secret-request storage and retryable external-staging cleanup.

These primitives are not authentication, encryption or credential installation.
Only the future authenticated Admin admission and target-isolated ARC-06 service
may call them. A target string is a consistency check, never a service identity.
The cleanup callback must remove only the named target-owned opaque object and
be idempotent when that object is already absent. No operational caller exists.
"""

import re
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID

from django.db import connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StorageInvariantError, UTCDateTimeField

from .secret_models import SECRET_TARGETS, SecretReplacementRequest


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
    expires_at,
    expected_fingerprint,
    correlation_id,
):
    """Record a trusted, already sealed staging reference with exact retry identity.

    Fresh Google authentication and bounded staging lifetime are admission policies
    owned by ARC-04/ARC-06; this storage layer enforces timestamp ordering only.
    An identical retry returns the original state, including after expiry/cleanup.
    """
    _identifiers(request_id, staging_reference, actor_id, correlation_id)
    _target(target)
    field = UTCDateTimeField()
    reauthenticated_at, expires_at = (
        field.to_python(reauthenticated_at),
        field.to_python(expires_at),
    )
    if reauthenticated_at is None or expires_at is None:
        raise ConfigError("Secret request timestamps are required.")
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
    )
    with _transaction():
        existing = SecretReplacementRequest.objects.filter(pk=request_id).first()
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in intent.items()):
                raise ConfigError("Secret request identity is already bound.")
            return _receipt(existing)
        now = _now()
        if reauthenticated_at > now or expires_at <= now:
            raise ConfigError("Invalid secret request interval.")
        if SecretReplacementRequest.objects.filter(
            target=target, state__in=["staged", "cleanup_pending"]
        ).exists():
            raise ConfigError("Credential target already has a pending request.")
        if SecretReplacementRequest.objects.filter(
            staging_reference=staging_reference
        ).exists():
            raise ConfigError("Staging reference is already bound.")
        return _receipt(
            SecretReplacementRequest.objects.create(
                id=request_id,
                actor_id=actor_id,
                correlation_id=correlation_id,
                **intent,
            )
        )


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
