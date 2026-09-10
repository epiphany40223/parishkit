"""Bounded retryable re-encryption without changing codes, tokens, or login scope.

These storage primitives run behind the isolated rotation service. They do not
install key files or authorize key retirement; backup dependencies and verified
consumer acknowledgement remain additional installer prerequisites.
"""

from dataclasses import dataclass

from django.db import transaction
from django.db.models import F, Q

from parishkit.stewardship.accounts.cryptography import (
    CryptographicError,
    GeneralKeyring,
    TokenPrivateKeyring,
    envelope_header,
)
from parishkit.stewardship.audit.models import AuditEvent

from .credential_keys import key_set_lock
from .credential_models import FamilyAccessToken, FamilyCampaign, RehearsalCredential
from .family_identity import code_context
from .link_tokens import token_context
from .rehearsals import code_context as rehearsal_code_context
from .rehearsals import token_context as rehearsal_token_context


@dataclass(frozen=True)
class CipherSource:
    """Closed registry; callers cannot nominate arbitrary tables or plaintext fields."""

    model: type
    field: str
    context: object


def _sources(ring):
    """General and token ciphertext migrations never share decryption authority."""
    if isinstance(ring, GeneralKeyring):
        return ring, (
            CipherSource(FamilyCampaign, "code_ciphertext", code_context),
            CipherSource(
                RehearsalCredential, "code_ciphertext", rehearsal_code_context
            ),
        )
    if isinstance(ring, TokenPrivateKeyring):
        return ring.public(), (
            CipherSource(FamilyAccessToken, "ciphertext", token_context),
            CipherSource(
                RehearsalCredential, "token_ciphertext", rehearsal_token_context
            ),
        )
    raise TypeError("Cipher migration requires its private-purpose keyring.")


def _size(value):
    """Cap combined work across all sources, not a separate cap per table."""
    if type(value) is not int or not 1 <= value <= 1000:
        raise ValueError("Cipher migration requires a bounded batch size.")
    return value


def reencrypt_batch(ring, *, admit, batch_size=500):
    """Re-encrypt locked rows under the current writer in one auditable transaction.

    A skipped locked row is not a completion signal: verify_ciphertexts must
    independently scan all retained envelopes before retirement. No plaintext
    token, manual code, or stable token digest changes during key migration.
    """
    _size(batch_size)
    inventory, sources = _sources(ring)
    if not callable(admit):
        raise TypeError("Cipher migration requires service admission.")
    with transaction.atomic(), key_set_lock(inventory):
        if admit() is not True:
            raise PermissionError("Cipher migration is not admitted.")
        count = 0
        for source in sources:
            if count == batch_size:
                break
            query = Q(pk__in=[])
            for key_id in ring.keys.keys() - {ring.active.id}:
                query |= Q(**{source.field + "__contains": '"kid":"' + key_id + '"'})
            rows = (
                source.model.objects.select_for_update(skip_locked=True)
                .filter(query)
                .order_by("pk")[: batch_size - count]
            )
            for row in rows:
                value = getattr(row, source.field)
                envelope_header(value, ring.algorithm)
                context = source.context(row.pk)
                plaintext = ring.decrypt(value, context=context)
                replacement = inventory.encrypt(plaintext, context=context)
                source.model.objects.filter(pk=row.pk).update(
                    **{source.field: replacement, "version": F("version") + 1}
                )
                count += 1
        if count:
            AuditEvent.objects.create(event_type="credential_cipher_batch_migrated")
        return count


def verify_ciphertexts(ring, *, admit):
    """Verify decryptability and key dependencies, bounded in memory, not sampled.

    Hold the key inventory stable for the complete verification transaction.
    The result is informational; retirement must repeat it under an exclusive
    key lock alongside backup/key inventory checks. This never returns plaintext.
    """
    inventory, sources = _sources(ring)
    if not callable(admit):
        raise TypeError("Cipher verification requires service admission.")
    with transaction.atomic(), key_set_lock(inventory):
        if admit() is not True:
            raise PermissionError("Cipher verification is not admitted.")
        counts = {key_id: 0 for key_id in ring.keys}
        for source in sources:
            rows = source.model.objects.filter(
                **{source.field + "__isnull": False}
            ).values_list("pk", source.field)
            for identifier, value in rows.iterator(chunk_size=500):
                key_id, _ = envelope_header(value, ring.algorithm)
                if key_id not in counts:
                    raise CryptographicError(
                        "Retained ciphertext requires a missing key."
                    )
                ring.decrypt(value, context=source.context(identifier))
                counts[key_id] += 1
        return counts
