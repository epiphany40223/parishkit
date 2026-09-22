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
    file and a truncated file are never confused.
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
    sink.write(MAGIC + json.dumps(header, sort_keys=True).encode() + b"\n")
    box, digest, total, index = SecretBox(key), hashlib.sha256(), 0, 0
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
        return header
    except (ValueError, KeyError, TypeError):
        raise SealError("The sealed backup header is malformed.") from None


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
    box, digest, total, index = SecretBox(key), hashlib.sha256(), 0, 0
    while True:
        frame = source.read(FRAME)
        if len(frame) < NONCE + SecretBox.MACBYTES:
            raise SealError("The sealed backup is truncated.")
        # The frame carries the nonce it was sealed with; it must be this
        # position's, so a moved or repeated chunk fails here, and anything
        # appended after the final short chunk is read into it and fails too.
        try:
            if frame[:NONCE] != _nonce(index):
                raise CryptoError
            chunk = box.decrypt(frame[NONCE:], _nonce(index))
        except CryptoError:
            raise SealError("The sealed backup failed authentication.") from None
        digest.update(chunk)
        total += len(chunk)
        sink.write(chunk)
        index += 1
        if len(chunk) < CHUNK:
            return header["kind"], total, digest.hexdigest()
