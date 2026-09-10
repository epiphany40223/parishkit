"""Independent versioned encryption, MAC, signing and sealed-box keyrings.

The public token type exposes encryption only: possession of the general key
cannot decrypt a retained link. File loading/service mount admission lives in
the isolated service boundary, not in these pure cryptographic primitives.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from types import MappingProxyType
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, PublicKey, SealedBox

MAX_PAYLOAD = 2 * 1024 * 1024
KEY_ID = re.compile(r"[a-zA-Z0-9_-]{1,48}\Z")
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ"


class CryptographicError(ValueError):
    """A safe failure with no input ciphertext, key, plaintext or provider detail."""


def encode(value):
    """Canonical URL-safe Base64, without ambiguous padding variants."""
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode(value):
    """Reject noncanonical encodings and bound input before decoding."""
    if type(value) is not str or len(value) > MAX_PAYLOAD * 2:
        raise CryptographicError("Invalid cryptographic envelope.")
    try:
        result = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except ValueError:
        raise CryptographicError("Invalid cryptographic envelope.") from None
    if encode(result) != value:
        raise CryptographicError("Invalid cryptographic envelope.")
    return result


def _bound(value, *, maximum=MAX_PAYLOAD):
    """Every cryptographic operation has an explicit memory/input bound."""
    if type(value) is not bytes or not 0 < len(value) <= maximum:
        raise CryptographicError("Invalid cryptographic input.")
    return value


def _context(context):
    """Owners bind ciphertext to a purpose and opaque object identity, not a path."""
    return _bound(context, maximum=512)


def _pack(*values):
    """Length delimiters prevent concatenation ambiguity across purpose boundaries."""
    return b"".join(len(value).to_bytes(4, "big") + value for value in values)


@dataclass(frozen=True)
class Key:
    """Key material never appears in repr/debug output or a public inventory."""

    id: str
    usage: str
    material: bytes = field(repr=False)

    def __post_init__(self):
        if type(self.id) is not str or not KEY_ID.fullmatch(self.id):
            raise CryptographicError("Invalid key identifier.")
        if type(self.material) is not bytes or len(self.material) != 32:
            raise CryptographicError("A 256-bit key is required.")

    @property
    def fingerprint(self):
        return hashlib.sha256(self.material).hexdigest()


class _Ring:
    """Immutable inventory with exactly one active writer and no duplicate keys."""

    usages = frozenset({"active", "decrypt-only"})
    kind = "abstract"

    def __init__(self, keys):
        keys = tuple(keys)
        if (
            not 1 <= len(keys) <= 32
            or any(
                not isinstance(key, Key) or key.usage not in self.usages for key in keys
            )
            or len({key.id for key in keys}) != len(keys)
            or len({key.material for key in keys}) != len(keys)
            or sum(key.usage == "active" for key in keys) != 1
        ):
            raise CryptographicError("Invalid keyring inventory.")
        self.keys = MappingProxyType({key.id: key for key in keys})
        self.active = next(key for key in keys if key.usage == "active")

    def inventory(self):
        """Safe status only; no service can use this inventory as key material."""
        return tuple(
            {
                "kind": self.kind,
                "id": key.id,
                "usage": key.usage,
                "fingerprint": key.fingerprint,
            }
            for key in self.keys.values()
        )


def _envelope(algorithm, key_id, payload):
    """Closed version/algorithm/key-ID header accompanies every ciphertext."""
    return json.dumps(
        {"v": 1, "alg": algorithm, "kid": key_id, "body": encode(payload)},
        separators=(",", ":"),
        sort_keys=True,
    )


def envelope_header(value, algorithm):
    """Parse only our canonical envelope, never permissive arbitrary JSON objects."""
    if type(value) is not str or len(value) > MAX_PAYLOAD * 2:
        raise CryptographicError("Invalid cryptographic envelope.")
    try:
        data = json.loads(value)
        if (
            type(data) is not dict
            or set(data) != {"v", "alg", "kid", "body"}
            or type(data["v"]) is not int
            or data["v"] != 1
            or data["alg"] != algorithm
            or type(data["kid"]) is not str
            or not KEY_ID.fullmatch(data["kid"])
        ):
            raise ValueError
        body = decode(data["body"])
        if value != _envelope(algorithm, data["kid"], body):
            raise ValueError
        return data["kid"], body
    except (ValueError, TypeError, KeyError):
        raise CryptographicError("Invalid cryptographic envelope.") from None


class GeneralKeyring(_Ring):
    """AES-256-GCM with independent random nonces and object/purpose-bound AAD."""

    kind = "general_encryption"
    algorithm = "aes256gcm-v1"

    def encrypt(self, value, *, context):
        """Randomized envelopes never reveal equality of retained manual codes."""
        _bound(value)
        aad = _pack(self.algorithm.encode(), self.active.id.encode(), _context(context))
        nonce = secrets.token_bytes(12)
        payload = AESGCM(self.active.material).encrypt(nonce, value, aad)
        return _envelope(self.algorithm, self.active.id, nonce + payload)

    def decrypt(self, value, *, context):
        """Read active/decrypt-only keys while preserving generic failure behavior."""
        key_id, body = envelope_header(value, self.algorithm)
        try:
            key = self.keys[key_id]
            aad = _pack(self.algorithm.encode(), key_id.encode(), _context(context))
            return AESGCM(key.material).decrypt(body[:12], body[12:], aad)
        except (KeyError, ValueError, InvalidTag):
            raise CryptographicError("Encrypted value is unavailable.") from None


class TokenPublicKeyring(_Ring):
    """Only public encryption keys: deliberately no retained-token decrypt method."""

    kind = "token_public"
    usages = frozenset({"active", "encryption-only"})
    algorithm = "sealedbox-v1"

    def encrypt(self, value, *, context):
        """Bind the encrypted plaintext to its opaque context as well as key ID."""
        payload = _pack(self.active.id.encode(), _context(context), _bound(value))
        encrypted = SealedBox(PublicKey(self.active.material)).encrypt(payload)
        return _envelope(self.algorithm, self.active.id, encrypted)


class TokenPrivateKeyring(_Ring):
    """Loaded only by dispatch or explicitly admitted key-rotation services."""

    kind = "token_private"
    algorithm = "sealedbox-v1"

    def public(self):
        """Safe distributable counterpart contains no private key material."""
        return TokenPublicKeyring(
            Key(
                key.id,
                "active" if key.usage == "active" else "encryption-only",
                bytes(PrivateKey(key.material).public_key),
            )
            for key in self.keys.values()
        )

    def decrypt(self, value, *, context):
        """Reject swapped record contexts after authenticated sealed-box decryption."""
        key_id, body = envelope_header(value, self.algorithm)
        try:
            payload = SealedBox(PrivateKey(self.keys[key_id].material)).decrypt(body)
            prefix = _pack(key_id.encode(), _context(context))
            if not payload.startswith(prefix) or len(payload) < len(prefix) + 4:
                raise ValueError
            size = int.from_bytes(payload[len(prefix) : len(prefix) + 4], "big")
            result = payload[len(prefix) + 4 :]
            if size != len(result):
                raise ValueError
            return _bound(result)
        except (KeyError, ValueError, CryptoError):
            raise CryptographicError("Encrypted value is unavailable.") from None


class CodeMacKeyring(_Ring):
    """Collision-only keys reserve historical codes but never authenticate them."""

    kind = "family_code_mac"
    usages = frozenset({"active", "lookup-only", "collision-only"})
    algorithm = "hmac-sha256-v1"

    def digest(self, key_id, campaign_id, code, *, reservation=False):
        """Domain-separate per-campaign authentication from anonymous reservations."""
        if not isinstance(campaign_id, UUID) or canonical_code(code) != code:
            raise CryptographicError("A canonical campaign/code pair is required.")
        try:
            key = self.keys[key_id]
        except KeyError:
            raise CryptographicError("Required collision key is unavailable.") from None
        if not reservation and key.usage == "collision-only":
            raise CryptographicError("Collision-only keys cannot authenticate.")
        purpose = b"rehearsal-reservation-v1" if reservation else b"family-code-v1"
        return hmac.new(
            key.material,
            _pack(purpose, campaign_id.bytes, code.encode("ascii")),
            hashlib.sha256,
        ).hexdigest()

    def lookups(self, campaign_id, code):
        """One candidate under every accepted key; no ciphertext scans or fallback."""
        return {
            key.id: self.digest(key.id, campaign_id, code)
            for key in self.keys.values()
            if key.usage in {"active", "lookup-only"}
        }


class SigningKeyring(_Ring):
    """Django's session/CSRF signing key plus explicit bounded fallback inventory."""

    kind = "django_signing"
    usages = frozenset({"active", "verify-only"})

    def django_settings(self):
        """Only startup applies these values; never return them to a status view."""
        return {
            "SECRET_KEY": encode(self.active.material),
            "SECRET_KEY_FALLBACKS": [
                encode(key.material)
                for key in self.keys.values()
                if key.usage == "verify-only"
            ],
        }


def independent_keyrings(*rings):
    """Reject secret reuse across purposes; public/private counterparts are expected."""
    seen = set()
    for ring in rings:
        if isinstance(ring, TokenPublicKeyring):
            continue
        for key in ring.keys.values():
            if key.material in seen:
                raise CryptographicError("Keyrings require independent key material.")
            seen.add(key.material)


def canonical_code(value):
    """Only ASCII spaces/hyphens are friendly delimiters; I/L/O remain candidates."""
    if type(value) is not str or len(value) > 128:
        return None
    value = value.replace(" ", "").replace("-", "")
    if len(value) != 8 or not value.isascii() or not value.isalpha():
        return None
    return value.upper()


def new_code(*, testing=False):
    """Mode-disjoint eight-letter codes with cryptographically secure sampling."""
    return ("I" if testing else "") + "".join(
        secrets.choice(ALPHABET) for _ in range(7 if testing else 8)
    )


def new_token(*, testing=False):
    """Independent 256-bit entropy; a rehearsal discriminator cannot collide live."""
    return ("test." if testing else "") + encode(secrets.token_bytes(32))


def token_digest(value, campaign_id):
    """Digest lookup needs no private key and includes campaign/mode separation."""
    if type(value) is not str or not isinstance(campaign_id, UUID):
        raise CryptographicError("Invalid link credential.")
    testing = value.startswith("test.")
    payload = decode(value[5:] if testing else value)
    if len(payload) != 32:
        raise CryptographicError("Invalid link credential.")
    return hashlib.sha256(
        _pack(
            b"family-link-v1",
            campaign_id.bytes,
            b"testing" if testing else b"production",
            payload,
        )
    ).hexdigest()
