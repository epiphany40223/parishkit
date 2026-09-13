"""Ephemeral, fence-bound credential transfer for an original setup source load.

The worker never receives the target installer's persistent handoff private key.
It creates a fresh recipient only in working memory. The isolated target owner
can relay the existing sealed candidate to that recipient without installing it
as a working credential. Losing the worker process loses its decryption key.

These cryptographic primitives do not authorize transfers. The setup exchange
owner must verify the original attempt, live Task/source fences and target SQL
identity before publishing or reading an exchange, and repeat them around every
provider call. No exchange persistence or runtime grant is enabled here.
"""

import hmac
import re
import secrets
from dataclasses import dataclass, field
from uuid import UUID

from nacl.exceptions import CryptoError

from parishkit.stewardship.accounts.credential_handoff import PrivateHandoff
from parishkit.stewardship.accounts.cryptography import (
    CryptographicError,
    Key,
    TokenPrivateKeyring,
    TokenPublicKeyring,
)
from parishkit.stewardship.jobs.ownership import TaskClaim

from .credentials import SourceCredential


@dataclass(frozen=True)
class SetupCredentialScope:
    """Bind all original identities, including worker and monotonically fenced Task."""

    attempt_id: UUID
    request_id: UUID
    claim: TaskClaim
    source_fence: int

    def __post_init__(self):
        """Never coerce externally supplied identifiers or boolean fences."""
        if (
            not isinstance(self.attempt_id, UUID)
            or not isinstance(self.request_id, UUID)
            or not isinstance(self.claim, TaskClaim)
            or type(self.source_fence) is not int
            or not 1 <= self.source_fence < 2**63
        ):
            raise CryptographicError("Invalid setup credential transfer scope.")

    def context(self):
        """Fixed-width identities form a distinct namespace from persistent handoff."""
        return (
            b"setup-source-credential-v1:"
            + self.attempt_id.bytes
            + self.request_id.bytes
            + self.claim.run_id.bytes
            + self.claim.worker_id.bytes
            + self.claim.fence.to_bytes(8, "big")
            + self.source_fence.to_bytes(8, "big")
        )


@dataclass(frozen=True)
class SetupCredentialRecipient:
    """Public recipient metadata; possession is not source-work authorization."""

    scope: SetupCredentialScope
    public_key: bytes = field(repr=False)

    def __post_init__(self):
        """Require one correctly sized fresh public key, not a stored keyring path."""
        if (
            not isinstance(self.scope, SetupCredentialScope)
            or type(self.public_key) is not bytes
            or len(self.public_key) != 32
        ):
            raise CryptographicError("Invalid setup credential recipient.")

    def ring(self):
        """Reuse the existing authenticated sealed-envelope implementation."""
        return TokenPublicKeyring([Key("setup-load-v1", "active", self.public_key)])


@dataclass(frozen=True)
class SealedSetupCredential:
    """Only ciphertext and a byte receipt cross the private-memory boundary."""

    ciphertext: str = field(repr=False)
    fingerprint: str = field(repr=False)


def _valid_fingerprint(value):
    """Reject malformed or non-ASCII receipt fields before constant-time comparison."""
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


class EphemeralSetupRecipient:
    """One worker invocation holds its only private key; there is no export API."""

    def __init__(self, scope):
        """Generate independent key material, never derive it from a live secret."""
        if not isinstance(scope, SetupCredentialScope):
            raise CryptographicError("Invalid setup credential transfer scope.")
        self._scope = scope
        self._private = TokenPrivateKeyring(
            [Key("setup-load-v1", "active", secrets.token_bytes(32))]
        )

    @property
    def public(self):
        """Expose only the public recipient bound to this invocation's exact fence."""
        return SetupCredentialRecipient(
            self._scope, self._private.public().active.material
        )

    def open(self, sealed):
        """A restarted worker or any changed scope cannot recover a prior transfer."""
        if not isinstance(sealed, SealedSetupCredential) or not _valid_fingerprint(
            sealed.fingerprint
        ):
            raise CryptographicError("Invalid setup credential transfer.")
        credential = SourceCredential(
            self._private.decrypt(sealed.ciphertext, context=self._scope.context())
        )
        if not hmac.compare_digest(credential.fingerprint, sealed.fingerprint):
            raise CryptographicError("Setup credential transfer receipt differs.")
        return credential


def relay_setup_credential(private, *, recipient, ciphertext, fingerprint):
    """The ParishSoft installer re-seals exactly its staged, fingerprint-bound bytes.

    No working credential file is touched. The caller must already hold the
    exchange owner's independent SQL/attempt/fence admission; this helper does
    not look up records, poll a queue, log values or infer authority from a UUID.
    """
    if (
        not isinstance(private, PrivateHandoff)
        or private.target != "parishsoft"
        or not isinstance(recipient, SetupCredentialRecipient)
        or not _valid_fingerprint(fingerprint)
    ):
        raise CryptographicError("Invalid setup credential relay.")
    credential = SourceCredential(private.open(recipient.scope.request_id, ciphertext))
    if not hmac.compare_digest(credential.fingerprint, fingerprint):
        raise CryptographicError("Setup credential relay receipt differs.")
    try:
        sealed = recipient.ring().encrypt(
            credential.value, context=recipient.scope.context()
        )
    except CryptoError:
        raise CryptographicError("Invalid setup credential recipient.") from None
    return SealedSetupCredential(sealed, credential.fingerprint)
