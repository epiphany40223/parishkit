"""Target-specific public sealing; the web type cannot decrypt prior credentials."""

from dataclasses import dataclass, field
from uuid import UUID

from .cryptography import (
    CryptographicError,
    Key,
    TokenPrivateKeyring,
    TokenPublicKeyring,
)
from .key_files import MAX_FILE_BYTES
from .secret_models import SECRET_TARGETS


def _context(target, request_id, purpose):
    """Bind target and immutable request identity inside the authenticated envelope."""
    if (
        target not in SECRET_TARGETS
        or not isinstance(request_id, UUID)
        or purpose not in {"candidate", "rollback"}
    ):
        raise CryptographicError("Invalid credential handoff identity.")
    return (
        b"credential-handoff-v1:"
        + target.encode("ascii")
        + b":"
        + request_id.bytes
        + b":"
        + purpose.encode("ascii")
    )


@dataclass(frozen=True)
class PublicHandoff:
    """A distributable per-target encryption key; no secret recovery method exists."""

    target: str
    key: Key = field(repr=False)

    def __post_init__(self):
        if (
            self.target not in SECRET_TARGETS
            or not isinstance(self.key, Key)
            or self.key.usage != "active"
        ):
            raise CryptographicError("Invalid public credential handoff key.")

    def seal(self, request_id, value, *, purpose="candidate"):
        """Immediately seal bounded plaintext; never retain it in a request object."""
        if type(value) is not bytes or not 0 < len(value) <= MAX_FILE_BYTES:
            raise CryptographicError("Credential replacement is empty or too large.")
        return TokenPublicKeyring([self.key]).encrypt(
            value,
            context=_context(self.target, request_id, purpose),
        )


@dataclass(frozen=True)
class PrivateHandoff:
    """Only a target installer loads its independent handoff private key."""

    target: str
    key: Key = field(repr=False)

    def __post_init__(self):
        if (
            self.target not in SECRET_TARGETS
            or not isinstance(self.key, Key)
            or self.key.usage != "active"
        ):
            raise CryptographicError("Invalid private credential handoff key.")

    def public(self):
        """Publish only the corresponding per-target public encryption material."""
        return PublicHandoff(
            self.target, TokenPrivateKeyring([self.key]).public().active
        )

    def open(self, request_id, ciphertext, *, purpose="candidate"):
        """Cross-request and cross-target swaps fail without exposing their values."""
        result = TokenPrivateKeyring([self.key]).decrypt(
            ciphertext,
            context=_context(self.target, request_id, purpose),
        )
        if len(result) > MAX_FILE_BYTES:
            raise CryptographicError("Credential replacement is too large.")
        return result
