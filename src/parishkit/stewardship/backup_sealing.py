"""Seal a backup stream to a human-held key, in bounded chunks, and open it again.

The v1 backup leaves the host encrypted to an X25519 public key whose private
half the parish's operator keeps off the host. A fresh 32-byte data key
protects the stream in fixed-size XSalsa20-Poly1305 chunks whose nonces are
their positions, so a chunk cannot be dropped, reordered or replayed without
detection; the data key travels in a sealed box only the private key opens.
The header names the key the file is for, so a wrong key is a clear refusal
rather than a corrupt restore. Nothing here reads configuration or the
database: the callers own what is sealed and where it goes.
"""

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path

from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, PublicKey, SealedBox
from nacl.secret import SecretBox
from nacl.utils import random

from parishkit.config import ConfigError

MAGIC = b"PKBK1\n"
CHUNK = 8 * 1024 * 1024
# A header is a few hundred bytes; anything larger is not one of ours.
MAX_HEADER = 4096
# Each chunk carries its 24-byte nonce and 16-byte tag.
NONCE = SecretBox.NONCE_SIZE
FRAME = NONCE + CHUNK + SecretBox.MACBYTES
# Chunk 0 is the header's digest; data chunks follow it.
HEADER_FRAME = NONCE + 32 + SecretBox.MACBYTES


class SealError(ConfigError):
    """A sealed backup could not be opened; the message never quotes data."""


def fingerprint(public):
    """A short stable name for a public key, safe to print and record."""
    if not isinstance(public, PublicKey):
        raise TypeError("A public key is required.")
    return hashlib.sha256(bytes(public)).hexdigest()[:16]


@dataclass(frozen=True)
class Recipient:
    """The public half of the human-held backup key."""

    public: PublicKey

    @property
    def fingerprint(self):
        return fingerprint(self.public)

    @classmethod
    def load(cls, path):
        """Read a public key file: 32 bytes as one base64 line, nothing else."""
        try:
            raw = base64.b64decode(Path(path).read_text().strip(), validate=True)
            if len(raw) != PublicKey.SIZE:
                raise ValueError
            return cls(PublicKey(raw))
        except (OSError, ValueError, UnicodeDecodeError):
            raise SealError(
                "The backup recipient key file is not a public key."
            ) from None


def generate_keypair():
    """A new X25519 pair as base64 lines: the private one leaves with the human."""
    private = PrivateKey.generate()
    return (
        base64.b64encode(bytes(private)).decode() + "\n",
        base64.b64encode(bytes(private.public_key)).decode() + "\n",
    )


def load_private(path):
    """Read the human's private key file, refusing anything but one key."""
    try:
        raw = base64.b64decode(Path(path).read_text().strip(), validate=True)
        if len(raw) != PrivateKey.SIZE:
            raise ValueError
        return PrivateKey(raw)
    except (OSError, ValueError, UnicodeDecodeError):
        raise SealError("The backup private key file is not a private key.") from None


def _nonce(index):
    """Chunk positions are the nonces: 24 bytes, big-endian, never repeated."""
    return index.to_bytes(NONCE, "big")


def seal(source, sink, *, recipient, kind):
    """Encrypt everything read from source into sink; return (bytes, sha256).

    The count and digest describe the plaintext, so a manifest can name what
    was backed up without keeping it. The final chunk may be short; a
    zero-length input still writes one empty authenticated chunk, so an empty
    file and a truncated file are never confused. The sealed box is anonymous:
    anyone holding the public key can produce a file that opens, so a set's
    origin is proved by the manifest digest the backup records, not by the
    file itself.
    """
    if not isinstance(recipient, Recipient) or kind not in {"database", "files"}:
        raise TypeError("A recipient and a known backup kind are required.")
    key = random(SecretBox.KEY_SIZE)
    header = {
        "version": 1,
        "kind": kind,
        "recipient": recipient.fingerprint,
        "chunk": CHUNK,
        "key": base64.b64encode(SealedBox(recipient.public).encrypt(key)).decode(),
    }
    line = json.dumps(header, sort_keys=True).encode() + b"\n"
    sink.write(MAGIC + line)
    box, digest, total = SecretBox(key), hashlib.sha256(), 0
    # Chunk 0 binds the header to the data key: a header rewritten around the
    # same chunks, or chunks moved under another header, fail to open.
    sink.write(box.encrypt(hashlib.sha256(line).digest(), _nonce(0)))
    index = 1
    while True:
        chunk = source.read(CHUNK)
        digest.update(chunk)
        total += len(chunk)
        sink.write(box.encrypt(chunk, _nonce(index)))
        index += 1
        if len(chunk) < CHUNK:
            break
    return total, digest.hexdigest()


def _header(source):
    """Read and validate the header line; refuse a file that is not ours."""
    if source.read(len(MAGIC)) != MAGIC:
        raise SealError("This is not a sealed backup.")
    line = source.readline(MAX_HEADER + 1)
    if not line.endswith(b"\n") or len(line) > MAX_HEADER:
        raise SealError("The sealed backup header is malformed.")
    try:
        header = json.loads(line)
        if header["version"] != 1 or header["chunk"] != CHUNK:
            raise ValueError
        header["key"] = base64.b64decode(header["key"], validate=True)
        header["digest"] = hashlib.sha256(line).digest()
        return header
    except (ValueError, KeyError, TypeError):
        raise SealError("The sealed backup header is malformed.") from None


def _frame(source, box, index, size):
    """Read and open one authenticated frame at its position."""
    frame = source.read(NONCE + size + SecretBox.MACBYTES)
    if len(frame) < NONCE + SecretBox.MACBYTES:
        raise SealError("The sealed backup is truncated.")
    # The frame carries the nonce it was sealed with; it must be this
    # position's, so a moved or repeated chunk fails here, and anything
    # appended after the final short chunk is read into it and fails too.
    try:
        if frame[:NONCE] != _nonce(index):
            raise CryptoError
        return box.decrypt(frame[NONCE:], _nonce(index))
    except CryptoError:
        raise SealError("The sealed backup failed authentication.") from None


def open_sealed(source, sink, *, private):
    """Decrypt a sealed backup; return (kind, bytes, sha256) of the plaintext.

    A wrong key, a changed byte, a missing or repeated chunk and a truncated
    file are all refused before any partial output is trusted: the caller
    should discard sink on failure.
    """
    if not isinstance(private, PrivateKey):
        raise TypeError("A private key is required.")
    header = _header(source)
    if header["recipient"] != fingerprint(private.public_key):
        raise SealError("The sealed backup is for a different key.")
    try:
        key = SealedBox(private).decrypt(header["key"])
    except CryptoError:
        raise SealError("The sealed backup key could not be opened.") from None
    box, digest, total = SecretBox(key), hashlib.sha256(), 0
    if _frame(source, box, 0, hashlib.sha256().digest_size) != header["digest"]:
        raise SealError("The sealed backup header is not the one sealed.")
    index = 1
    while True:
        chunk = _frame(source, box, index, CHUNK)
        digest.update(chunk)
        total += len(chunk)
        sink.write(chunk)
        index += 1
        if len(chunk) < CHUNK:
            return header["kind"], total, digest.hexdigest()


# A key-possession proof: the Admin portal seals a short random code to a
# pasted public key, and only the matching private key, on the operator's key
# machine, can print it again (``backup-prove``). Crockford base32 without
# the easily confused letters; twelve characters are 60 random bits, far
# beyond guessing within the page's one-hour window.
PROOF_PREFIX = "PKBKP1:"
PROOF_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PROOF_LENGTH = 12
# Sealed boxes add a 32-byte ephemeral key and a 16-byte tag.
MAX_PROOF_TEXT = len(PROOF_PREFIX) + 4 * ((PROOF_LENGTH + 48 + 2) // 3)


# X25519 public keys are field elements below 2^255 - 19, so a canonical one
# never has the top bit of its last byte set. Private keys are random bytes
# (libsodium clamps them only when used), so about half of them do.
FIELD_PRIME = 2**255 - 19


def looks_private(text):
    """True when pasted text is 32 bytes no public key could be: a private key.

    Catches about half of mistaken private-key pastes without knowing the
    pair; the rest are caught by the proof, which such a key cannot pass.
    """
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (AttributeError, ValueError, TypeError):
        return False
    return len(raw) == PublicKey.SIZE and bool(raw[31] & 0x80)


def parse_public_key(text):
    """Admit one pasted public key: 32 bytes as one canonical base64 line.

    Anything else is refused, including the whole JSON line
    ``backup-keygen`` prints and any non-canonical encoding (the top bit
    set, or a value of at least 2^255 - 19). A private key's text has the
    same shape, so the page warns never to paste one. libsodium refuses to
    seal to the all-zero and other low-order points, so a trial seal rejects
    keys no private key could open.
    Returns the ``Recipient``; its canonical text is ``public_text``.
    """
    try:
        value = text.strip()
        raw = base64.b64decode(value, validate=True)
        if len(raw) != PublicKey.SIZE or base64.b64encode(raw).decode() != value:
            raise ValueError
        # Only the canonical encoding: another encoding of the same point
        # would get a fingerprint its private key never reports.
        if int.from_bytes(raw, "little") >= FIELD_PRIME:
            raise ValueError
        public = PublicKey(raw)
        SealedBox(public).encrypt(b"\0")
    except (AttributeError, ValueError, TypeError, RuntimeError, CryptoError):
        raise SealError("This is not a backup public key.") from None
    return Recipient(public)


def public_text(recipient):
    """The canonical one-line base64 form a public key is stored in."""
    return base64.b64encode(bytes(recipient.public)).decode()


def seal_proof(recipient):
    """Return ``(challenge, code)``: a random code and its sealed text."""
    code = "".join(secrets.choice(PROOF_ALPHABET) for _ in range(PROOF_LENGTH))
    sealed = SealedBox(recipient.public).encrypt(code.encode("ascii"))
    return PROOF_PREFIX + base64.b64encode(sealed).decode(), code


def normalize_code(text):
    """Read a typed code leniently: any case, spaces or dashes, O for 0, I/L for 1."""
    value = "".join(text.split()).replace("-", "").upper()
    return value.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))


def display_code(code):
    """Group a code in fours so it is easy to read aloud and type."""
    return "-".join(code[index : index + 4] for index in range(0, len(code), 4))


def open_proof(text, private):
    """Open a portal challenge with the private key; return its code.

    A challenge sealed to any other key, or anything that is not a challenge,
    is one clear refusal: the Administrator pasted the wrong key or text.
    """
    if not isinstance(private, PrivateKey):
        raise TypeError("A private key is required.")
    value = text.strip()
    try:
        if not value.startswith(PROOF_PREFIX) or len(value) > MAX_PROOF_TEXT:
            raise ValueError
        sealed = base64.b64decode(value.removeprefix(PROOF_PREFIX), validate=True)
        code = SealedBox(private).decrypt(sealed).decode("ascii")
    except (ValueError, CryptoError):
        raise SealError("This challenge was not sealed to this key.") from None
    if len(code) != PROOF_LENGTH or any(c not in PROOF_ALPHABET for c in code):
        raise SealError("This challenge was not sealed to this key.")
    return code
