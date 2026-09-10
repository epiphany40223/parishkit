"""Coherent key inventories and a stable MAC set during population/migration.

These are internal installer/storage operations. Runtime mount admission owns
who may publish inventories; possession of a digest never grants that authority.
"""

import hashlib
import json
from contextlib import contextmanager

from django.db import connection, transaction
from django.db.models import F

from parishkit.stewardship.accounts.cryptography import (
    CryptographicError,
    independent_keyrings,
)
from parishkit.stewardship.audit.models import AuditEvent

from .credential_models import CredentialKeyState, DeploymentCredentialState

KEY_LOCK = (736226, 1)


def inventory_digest(ring):
    """Stable non-secret digest covers key IDs, material fingerprints and usages."""
    return hashlib.sha256(
        json.dumps(
            sorted(ring.inventory(), key=lambda item: item["id"]),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()


@contextmanager
def key_set_lock(*rings, exclusive=False):
    """Hold one accepted inventory stable throughout a surrounding transaction."""
    if not connection.in_atomic_block or connection.vendor != "postgresql":
        raise CryptographicError(
            "Credential operations require PostgreSQL transactions."
        )
    function = (
        "pg_try_advisory_xact_lock" if exclusive else "pg_try_advisory_xact_lock_shared"
    )
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {function}(%s, %s)", KEY_LOCK)
        if not cursor.fetchone()[0]:
            # Do not queue a new reader behind a rotation writer while holding
            # another owner's runtime lock. The caller retries its transaction.
            raise CryptographicError("Credential key rotation is busy; retry.")
    for ring in rings:
        if not CredentialKeyState.objects.filter(
            kind=ring.kind, inventory_digest=inventory_digest(ring)
        ).exists():
            raise CryptographicError("Credential key inventory is not current.")
    yield


def initialize_key_inventories(*rings):
    """Idempotent empty-store bootstrap only; replacement uses the rotation owner."""
    independent_keyrings(*rings)
    with transaction.atomic(), key_set_lock(exclusive=True):
        DeploymentCredentialState.objects.get_or_create()
        for ring in rings:
            _check_independent_inventory(ring)
            row, created = CredentialKeyState.objects.get_or_create(
                kind=ring.kind,
                defaults={
                    "inventory_digest": inventory_digest(ring),
                    "inventory": list(ring.inventory()),
                },
            )
            if not created and row.inventory_digest != inventory_digest(ring):
                raise CryptographicError("Existing keys require a reviewed rotation.")
            if created:
                AuditEvent.objects.create(
                    event_type="credential_key_initialized", subject_id=row.pk
                )


def add_rotation_key(previous, replacement):
    """Add/switch writers while retaining every previous key and material binding.

    Retirement and lookup-to-collision-only changes require the later verified
    migration operation, never this installation primitive alone.
    """
    if previous.kind != replacement.kind:
        raise CryptographicError("Keyring purpose cannot change.")
    for key in previous.keys.values():
        new = replacement.keys.get(key.id)
        allowed = {key.usage}
        if key.usage == "active":
            allowed.update(
                {"decrypt-only", "lookup-only", "verify-only", "encryption-only"}
            )
        if new is None or new.material != key.material or new.usage not in allowed:
            raise CryptographicError("Rotation must preserve retained keys.")
    with transaction.atomic(), key_set_lock(previous, exclusive=True):
        _check_independent_inventory(replacement)
        row = CredentialKeyState.objects.get(kind=previous.kind)
        CredentialKeyState.objects.filter(pk=row.pk).update(
            inventory=list(replacement.inventory()),
            inventory_digest=inventory_digest(replacement),
            version=F("version") + 1,
        )
        AuditEvent.objects.create(
            event_type="credential_key_activated", subject_id=row.pk
        )


def _check_independent_inventory(ring):
    """Compare fingerprints with every other installed purpose under the key lock.

    Single-target installation cannot rely on having unrelated private keyrings
    in memory. Inventories allow checking without unrelated decryption access.
    """
    fingerprints = {key.fingerprint for key in ring.keys.values()}
    for inventory in CredentialKeyState.objects.exclude(kind=ring.kind).values_list(
        "inventory", flat=True
    ):
        if any(item["fingerprint"] in fingerprints for item in inventory):
            raise CryptographicError("Keyrings require independent key material.")
