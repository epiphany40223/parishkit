"""Ephemeral Workspace relay to one mail-worker claim, never general workers.

These primitives have no database or provider authority. Their exchange owner
must independently prove the original setup, exact journal and live Task claim.
The isolated Workspace installer opens its existing staged candidate and seals
it to a new recipient held only in the mail worker's memory. No working file or
persistent recipient private key is created by this transfer.
"""

import hmac
import re
import secrets
from dataclasses import dataclass, field
from uuid import UUID

from parishkit.stewardship.jobs.ownership import TaskClaim

from .credential_handoff import PrivateHandoff
from .cryptography import (
    CryptographicError,
    Key,
    TokenPrivateKeyring,
    TokenPublicKeyring,
)
from .key_files import MAX_FILE_BYTES, file_fingerprint

_KEY_ID = "setup-mail-v1"


@dataclass(frozen=True)
class MailCredentialScope:
    """The immutable mail journal binds the remaining setup and recipient metadata."""

    delivery_id: UUID
    credential_id: UUID
    credential_version: int
    claim: TaskClaim

    def __post_init__(self):
        """Only typed fixed-width identities may enter the authenticated namespace."""
        if (
            not isinstance(self.delivery_id, UUID)
            or not isinstance(self.credential_id, UUID)
            or type(self.credential_version) is not int
            or not 1 <= self.credential_version < 2**63
            or not isinstance(self.claim, TaskClaim)
        ):
            raise CryptographicError("Invalid setup mail transfer scope.")

    def context(self):
        """Separate mail credentials from ParishSoft, rollback and ordinary handoffs."""
        return (
            b"setup-mail-credential-v1:"
            + self.delivery_id.bytes
            + self.credential_id.bytes
            + self.credential_version.to_bytes(8, "big")
            + self.claim.run_id.bytes
            + self.claim.worker_id.bytes
            + self.claim.fence.to_bytes(8, "big")
        )


@dataclass(frozen=True)
class MailCredentialRecipient:
    """Fresh public key only; knowing it does not authorize a delivery or transfer."""

    scope: MailCredentialScope
    public_key: bytes = field(repr=False)

    def __post_init__(self):
        """Reject malformed keys before constructing a sealed envelope."""
        if (
            not isinstance(self.scope, MailCredentialScope)
            or type(self.public_key) is not bytes
            or len(self.public_key) != 32
        ):
            raise CryptographicError("Invalid setup mail recipient.")

    def ring(self):
        """Reuse the reviewed authenticated-envelope implementation."""
        return TokenPublicKeyring([Key(_KEY_ID, "active", self.public_key)])


@dataclass(frozen=True, repr=False)
class WorkspaceCandidate:
    """Private bytes are bounded here; the finite provider helper validates syntax."""

    value: bytes

    def __post_init__(self):
        """Never accept empty, coerced or oversized credential plaintext."""
        if type(self.value) is not bytes or not 0 < len(self.value) <= MAX_FILE_BYTES:
            raise CryptographicError("Invalid Workspace credential transfer.")

    @property
    def fingerprint(self):
        """Use the same exact-byte receipt as target installation and consumer ACK."""
        return file_fingerprint(self.value)


@dataclass(frozen=True, repr=False)
class SealedMailCredential:
    """Ciphertext is temporary; neither it nor its receipt belongs in diagnostics."""

    ciphertext: str
    fingerprint: str


def _fingerprint(value):
    """Validate a fixed-size ASCII receipt before constant-time comparisons."""
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise CryptographicError("Invalid Workspace credential receipt.")
    return value


class EphemeralMailRecipient:
    """Losing a worker process loses this key; a new process cannot resume it."""

    def __init__(self, scope):
        """Create independent random recipient material, not a derived provider key."""
        if not isinstance(scope, MailCredentialScope):
            raise CryptographicError("Invalid setup mail transfer scope.")
        self._scope = scope
        self._private = TokenPrivateKeyring(
            [Key(_KEY_ID, "active", secrets.token_bytes(32))]
        )

    @property
    def public(self):
        """Expose no persistent path or private-key export interface."""
        return MailCredentialRecipient(
            self._scope, self._private.public().active.material
        )

    def open(self, sealed):
        """Swapped attempts, revisions, fences, recipients and receipts all fail."""
        if not isinstance(sealed, SealedMailCredential):
            raise CryptographicError("Invalid setup mail credential envelope.")
        fingerprint = _fingerprint(sealed.fingerprint)
        candidate = WorkspaceCandidate(
            self._private.decrypt(sealed.ciphertext, context=self._scope.context())
        )
        if not hmac.compare_digest(candidate.fingerprint, fingerprint):
            raise CryptographicError("Workspace credential receipt differs.")
        return candidate


def relay_mail_credential(private, *, recipient, ciphertext, fingerprint):
    """Only Workspace's isolated target can relay its exact staged candidate.

    The caller owns fresh SQL authorization. This helper does not make a mail
    request, install credentials, search for files or persist private material.
    """
    if (
        not isinstance(private, PrivateHandoff)
        or private.target != "google_workspace"
        or not isinstance(recipient, MailCredentialRecipient)
    ):
        raise CryptographicError("Invalid setup mail credential relay.")
    fingerprint = _fingerprint(fingerprint)
    candidate = WorkspaceCandidate(
        private.open(recipient.scope.credential_id, ciphertext)
    )
    if not hmac.compare_digest(candidate.fingerprint, fingerprint):
        raise CryptographicError("Workspace credential receipt differs.")
    return SealedMailCredential(
        recipient.ring().encrypt(candidate.value, context=recipient.scope.context()),
        candidate.fingerprint,
    )
