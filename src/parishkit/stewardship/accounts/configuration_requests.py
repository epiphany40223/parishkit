"""Internal durable request intake, not an authorization or installer API.

No route, CLI, or worker exposes these storage primitives. The future Admin
boundary must authenticate, check current role/CSRF, and perform active-version
admission on every call, including lookup and retry. Actor equality here prevents
cross-actor key/status collisions; a UUID is attribution, never proof of Admin
authority. Offline recovery has no caller-supplied bypass and is not admitted.

Only intake/cancellation is implemented. Requests never write authority files,
prepare candidates, change active configuration, or claim Applied. Historical
prepared bases can be recorded; the installer must reject a stale active base
before manifest selection. No implicit rebase occurs in this layer.
"""

import hashlib
import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from django.db import connection, transaction

from parishkit.config import ConfigError
from parishkit.stewardship.storage import StaleRecordError, StorageInvariantError

from .request_admission import check_historical_additions, intake_base
from .request_models import ConfigurationChangeRequest, ConfigurationRequestCheckpoint
from .request_patch import REQUEST_SCHEMA, build_candidate


@dataclass(frozen=True)
class RequestStatus:
    """Safe intake receipt; candidate identity is never an applied-version claim."""

    request_id: UUID
    state: str
    sequence: int
    base_digest: str
    candidate_version_id: UUID
    candidate_digest: str


def _identities(*values):
    """Require explicit opaque identities rather than coerce caller input."""
    if any(not isinstance(value, UUID) for value in values):
        raise TypeError("Explicit UUID identities are required.")


def _own_transaction():
    """A returned receipt must survive caller rollback and release its locks."""
    if connection.in_atomic_block or not connection.get_autocommit():
        raise StorageInvariantError("Request intake must own its transaction.")


def _status(request):
    """Read one atomic latest checkpoint; absent state fails closed."""
    checkpoint = request.checkpoints.order_by("-sequence").first()
    if checkpoint is None:
        raise ConfigError("Configuration request has no durable checkpoint.")
    return RequestStatus(
        request.pk,
        checkpoint.state,
        checkpoint.sequence,
        request.base.digest,
        request.candidate_version_id,
        request.candidate_digest,
    )


def _checkpoint(request, *, sequence, state, actor_id, correlation_id):
    """Append state; PostgreSQL atomically adds safe audit metadata for every row."""
    ConfigurationRequestCheckpoint.objects.create(
        request=request,
        sequence=sequence,
        state=state,
        actor_id=actor_id,
        correlation_id=correlation_id,
    )


def record_request(*, base_digest, patch, actor_id, request_key, correlation_id):
    """Persist one validated intent; identical actor/key retries return its state.

    The base check is bounded to one snapshot, not an entire canonical lineage.
    Schema selection and patch validation occur under the per-key lock so a
    concurrent winner always determines the frozen retry format. No current-
    authority or role policy is inferred from a prepared base or a key.
    """
    _identities(actor_id, request_key, correlation_id)
    _own_transaction()
    if (
        type(base_digest) is not str
        or re.fullmatch(r"[0-9a-f]{64}", base_digest) is None
    ):
        raise ConfigError("A valid base configuration digest is required.")
    if type(patch) is not list or not 1 <= len(patch) <= 100:
        raise ConfigError("Invalid or unsupported configuration patch.")
    base, version = intake_base(base_digest)
    key = int.from_bytes(
        hashlib.sha256(actor_id.bytes + request_key.bytes).digest()[:4],
        "big",
        signed=True,
    )
    with transaction.atomic(durable=True):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [736211, key])
        existing = (
            ConfigurationChangeRequest.objects.select_related("base")
            .filter(actor_id=actor_id, request_key=request_key)
            .first()
        )
        schema = existing.request_schema if existing is not None else REQUEST_SCHEMA
        intent = build_candidate(
            version, patch, candidate_id=uuid4(), request_schema=schema
        )
        if existing is not None:
            if existing.payload_fingerprint != intent.payload_fingerprint:
                raise ConfigError("Request key is already bound to another intent.")
            return _status(existing)
        check_historical_additions(base.pk, intent.patch())
        request = ConfigurationChangeRequest.objects.create(
            base=base,
            patch=intent.patch(),
            actor_id=actor_id,
            request_key=request_key,
            request_schema=schema,
            correlation_id=correlation_id,
            payload_fingerprint=intent.payload_fingerprint,
            candidate_version_id=intent.candidate.version_id,
            candidate_digest=intent.candidate.digest,
        )
        # PostgreSQL inserts the initial checkpoint/audit in the same statement.
        return _status(request)


def request_status(*, request_id, actor_id):
    """Lookup is actor-scoped even though the future caller must also authorize."""
    _identities(request_id, actor_id)
    request = (
        ConfigurationChangeRequest.objects.select_related("base")
        .filter(pk=request_id, actor_id=actor_id)
        .first()
    )
    if request is None:
        raise LookupError("Configuration request is unavailable.")
    return _status(request)


def cancel_request(*, request_id, actor_id, expected_sequence, correlation_id):
    """Cancel only staged intake; repeat delivery returns the original checkpoint."""
    _identities(request_id, actor_id, correlation_id)
    if type(expected_sequence) is not int or expected_sequence < 1:
        raise TypeError("An explicit positive sequence is required.")
    _own_transaction()
    with transaction.atomic(durable=True):
        request = (
            ConfigurationChangeRequest.objects.select_for_update(of=("self",))
            .select_related("base")
            .filter(pk=request_id, actor_id=actor_id)
            .first()
        )
        if request is None:
            raise LookupError("Configuration request is unavailable.")
        status = _status(request)
        if status.state == "cancelled" and status.sequence == expected_sequence + 1:
            return status
        if status.sequence != expected_sequence:
            raise StaleRecordError("Configuration request state has changed.")
        if status.state != "staged":
            raise ConfigError("Configuration request cannot be cancelled.")
        _checkpoint(
            request,
            sequence=status.sequence + 1,
            state="cancelled",
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
        return _status(request)
