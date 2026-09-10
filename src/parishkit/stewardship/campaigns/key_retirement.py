"""Verified inventory retirement with fail-closed backup and consumer owner ports.

This service changes online inventory, never deletes key files or escrow. The
installer must retain old files until its acknowledgement protocol completes.
OPS backup/signing owners supply a locked proof; there is no default proof for
an absent backup catalog and no web-supplied authorization token.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.db.models import Exists, F, OuterRef
from django.utils import timezone

from parishkit.stewardship.accounts.cryptography import (
    CodeMacKeyring,
    CryptographicError,
    GeneralKeyring,
    SigningKeyring,
    TokenPrivateKeyring,
)
from parishkit.stewardship.accounts.sessions import database_now
from parishkit.stewardship.audit.models import AuditEvent

from .cipher_rotation import verify_ciphertexts
from .credential_keys import inventory_digest, key_set_lock
from .credential_models import (
    CredentialKeyState,
    FamilyCampaign,
    FamilyCodeFingerprint,
    RehearsalCodeFingerprint,
    RehearsalCodeReservation,
    RehearsalCredential,
)


@dataclass(frozen=True)
class RetirementProof:
    """Owner-produced evidence, held stable until the surrounding commit.

    Backup compatibility means every retained backup is either migrated or
    paired with tested recoverable escrow, including collision-only MAC keys.
    Consumers must already have stopped old-key issuance. For signing keys,
    verification_ends_at is the last possible expiry of any old-key credential,
    not merely a key-file installation timestamp. A missing owner denies work.
    """

    previous_digest: str
    replacement_digest: str
    backups_compatible: bool
    consumers_ready: bool
    verification_ends_at: datetime | None = None


def _inventory(ring):
    """Only the private token rotation owner may verify sealed ciphertext."""
    if isinstance(ring, TokenPrivateKeyring):
        return ring.public()
    if isinstance(ring, (GeneralKeyring, CodeMacKeyring, SigningKeyring)):
        return ring
    raise TypeError("Retirement requires its private-purpose keyring.")


def _removed(previous, replacement):
    """Retirement only removes inactive keys; it cannot also change a writer."""
    if type(previous) is not type(replacement) or previous.active != replacement.active:
        raise CryptographicError("Retirement cannot change key purpose or writer.")
    removed = previous.keys.keys() - replacement.keys.keys()
    if not removed or replacement.keys.keys() - previous.keys.keys():
        raise CryptographicError("Retirement must only remove inactive keys.")
    if any(previous.keys[key] != value for key, value in replacement.keys.items()):
        raise CryptographicError("Retirement must preserve remaining key bindings.")
    return removed


def _verify_mac(previous, removed):
    """Anonymous non-reuse reservations cannot be migrated from their HMAC alone."""
    if any(previous.keys[key].usage != "collision-only" for key in removed):
        raise CryptographicError("MAC retirement requires verified login demotion.")
    if RehearsalCodeReservation.objects.filter(key_id__in=removed).exists():
        raise CryptographicError("Retained rehearsal reservations require this key.")
    families = FamilyCodeFingerprint.objects.filter(
        family_id=OuterRef("pk"), key_id=previous.active.id
    )
    rehearsals = RehearsalCodeFingerprint.objects.filter(
        credential_id=OuterRef("pk"), key_id=previous.active.id
    )
    if (
        FamilyCampaign.objects.filter(code_ciphertext__isnull=False)
        .filter(~Exists(families))
        .exists()
        or RehearsalCredential.objects.filter(~Exists(rehearsals)).exists()
    ):
        raise CryptographicError("Active-key authentication backfill is incomplete.")


def retire_keys(previous, replacement, *, admit, dependencies):
    """Recheck all dependencies under exclusive inventory and owner locks.

    dependencies(previous, replacement) is a required context-manager factory.
    It runs inside the retirement transaction and must acquire transaction-scoped
    database locks fencing backup creation/escrow destruction and consumer
    evidence. Those locks must survive context exit until transaction commit.
    No provider I/O belongs inside this transaction. The future operational
    command must supply real owners; callers cannot assume an empty catalog.
    """
    old_inventory, new_inventory = _inventory(previous), _inventory(replacement)
    removed = _removed(previous, replacement)
    if not callable(admit) or not callable(dependencies):
        raise TypeError("Retirement requires admission and locked owner evidence.")
    # Enter the transaction before owner locks, then take the key inventory lock.
    # PostgreSQL retains the owner's transaction locks through the outer commit.
    with (
        transaction.atomic(durable=True),
        dependencies(old_inventory, new_inventory) as proof,
        key_set_lock(old_inventory, exclusive=True),
    ):
        if admit() is not True:
            raise PermissionError("Key retirement is not admitted.")
        if (
            not isinstance(proof, RetirementProof)
            or proof.previous_digest != inventory_digest(old_inventory)
            or proof.replacement_digest != inventory_digest(new_inventory)
            or proof.backups_compatible is not True
            or proof.consumers_ready is not True
        ):
            raise CryptographicError("Key retirement dependencies are unavailable.")
        if isinstance(previous, SigningKeyring):
            end = proof.verification_ends_at
            if (
                not isinstance(end, datetime)
                or timezone.is_naive(end)
                or end > database_now()
            ):
                raise CryptographicError("Signing verification window remains open.")
        elif isinstance(previous, CodeMacKeyring):
            _verify_mac(previous, removed)
        else:
            counts = verify_ciphertexts(previous, admit=admit)
            if any(counts[key] for key in removed):
                raise CryptographicError("Retained ciphertext requires this key.")
        row = CredentialKeyState.objects.get(kind=old_inventory.kind)
        CredentialKeyState.objects.filter(pk=row.pk).update(
            inventory=list(new_inventory.inventory()),
            inventory_digest=inventory_digest(new_inventory),
            version=F("version") + 1,
        )
        AuditEvent.objects.create(
            event_type="credential_keys_retired", subject_id=row.pk
        )
    return frozenset(removed)
