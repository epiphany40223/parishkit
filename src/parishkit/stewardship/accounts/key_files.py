"""Bounded owner-only key files with strict, purpose-specific serialization.

Loading is subordinate to the service's mount admission. Neither a path nor a
requested keyring type grants a process access to another service's credential.
"""

import hashlib
import json
import os
import stat
from pathlib import Path

from parishkit.files import atomic_write_text

from .cryptography import (
    CodeMacKeyring,
    CryptographicError,
    GeneralKeyring,
    Key,
    SigningKeyring,
    TokenPrivateKeyring,
    TokenPublicKeyring,
    decode,
    encode,
)

KEYRINGS = {
    ring.kind: ring
    for ring in (
        GeneralKeyring,
        CodeMacKeyring,
        SigningKeyring,
        TokenPrivateKeyring,
        TokenPublicKeyring,
    )
}
MAX_FILE_BYTES = 128 * 1024


def _path(path):
    """No symlink traversal, relative paths, or accidentally broad file targets."""
    path = Path(path)
    if (
        not path.is_absolute()
        or path == Path(path.anchor)
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise CryptographicError("Credential path must be an explicit absolute file.")
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise CryptographicError("Credential paths cannot traverse symbolic links.")
    return path


def read_private(path):
    """Open once without following the last link, then validate the actual inode."""
    descriptor = None
    try:
        path = _path(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= MAX_FILE_BYTES
        ):
            raise CryptographicError(
                "Credential file ownership, mode or size is invalid."
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            result = stream.read(MAX_FILE_BYTES + 1)
        if not 0 < len(result) <= MAX_FILE_BYTES:
            raise CryptographicError("Credential file size is invalid.")
        return result
    except OSError:
        raise CryptographicError("Credential file is unavailable.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def write_private(path, value):
    """Use the shared atomic writer only inside a pre-provisioned private target.

    Parent fsync makes the rename durable. A write error may follow a committed
    rename: the installer journal must reconcile its fingerprint, never assume
    an exception proves the prior file remained selected.
    """
    if type(value) is not bytes or not 0 < len(value) <= MAX_FILE_BYTES:
        raise CryptographicError("Credential bytes must be nonempty and bounded.")
    try:
        text = value.decode("utf-8")
        path = _path(path)
        parent = path.parent.stat()
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.geteuid()
            or stat.S_IMODE(parent.st_mode) != 0o700
        ):
            raise CryptographicError("Credential target must be owner-only storage.")
        if path.exists():
            read_private(path)
        atomic_write_text(path, text)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except (OSError, UnicodeError):
        raise CryptographicError(
            "Credential replacement could not be completed."
        ) from None


def _unique_object(pairs):
    """Duplicate JSON keys never select a different interpretation in a consumer."""
    result = {}
    for name, value in pairs:
        if name in result:
            raise CryptographicError("Credential document repeats a field.")
        result[name] = value
    return result


def parse_keyring(value, kind):
    """Only the named independent key purpose may consume this exact document."""
    if kind not in KEYRINGS or type(value) is not bytes or len(value) > MAX_FILE_BYTES:
        raise CryptographicError("Unsupported keyring input.")
    try:
        data = json.loads(value, object_pairs_hook=_unique_object)
        if (
            type(data) is not dict
            or set(data) != {"version", "kind", "keys"}
            or type(data["version"]) is not int
            or data["version"] != 1
            or data["kind"] != kind
            or type(data["keys"]) is not list
            or not 1 <= len(data["keys"]) <= 32
        ):
            raise ValueError
        keys = []
        for entry in data["keys"]:
            if type(entry) is not dict or set(entry) != {"id", "usage", "key"}:
                raise ValueError
            keys.append(Key(entry["id"], entry["usage"], decode(entry["key"])))
        return KEYRINGS[kind](keys)
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        raise CryptographicError("Credential keyring is invalid.") from None


def serialize_keyring(ring):
    """Private serialization for the isolated installer, never a status response."""
    if type(ring) not in KEYRINGS.values():
        raise CryptographicError("Unsupported keyring type.")
    return json.dumps(
        {
            "version": 1,
            "kind": ring.kind,
            "keys": [
                {"id": key.id, "usage": key.usage, "key": encode(key.material)}
                for key in sorted(ring.keys.values(), key=lambda key: key.id)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def load_keyring(path, kind):
    """Service startup validates the mount before asking for a purpose-bound ring."""
    return parse_keyring(read_private(path), kind)


def file_fingerprint(value):
    """Safe status/acknowledgement binds every byte of the installed document."""
    if type(value) is not bytes or not 0 < len(value) <= MAX_FILE_BYTES:
        raise CryptographicError("Credential bytes must be nonempty and bounded.")
    return hashlib.sha256(value).hexdigest()
