"""Private file shape and purpose boundaries use synthetic keys only."""

import json
import os
from pathlib import Path

import pytest

from parishkit.stewardship.accounts.cryptography import (
    CryptographicError,
    GeneralKeyring,
    Key,
    TokenPrivateKeyring,
)
from parishkit.stewardship.accounts.key_files import (
    file_fingerprint,
    load_keyring,
    parse_keyring,
    read_private,
    serialize_keyring,
    write_private,
)


def test_private_file_roundtrip_and_atomic_replacement(tmp_path):
    root = tmp_path.resolve()
    root.chmod(0o700)
    path = root / "keyring.json"
    ring = GeneralKeyring([Key("general", "active", b"g" * 32)])
    raw = serialize_keyring(ring)
    write_private(path, raw)
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_keyring(path, "general_encryption").active.material == b"g" * 32
    assert len(file_fingerprint(raw)) == 64
    old_inode = path.stat().st_ino
    write_private(path, raw)
    assert path.stat().st_ino != old_inode
    assert list(root.iterdir()) == [path]


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660, 0o400, 0o777])
def test_wrong_modes_are_never_silently_repaired(tmp_path, mode):
    path = tmp_path.resolve() / "private"
    path.write_bytes(b"synthetic-secret")
    path.chmod(mode)
    with pytest.raises(CryptographicError, match="ownership"):
        read_private(path)


def test_links_and_broad_paths_rejected(tmp_path):
    root = tmp_path.resolve()
    root.chmod(0o700)
    path = root / "private"
    write_private(path, b"synthetic-secret")
    link = root / "link"
    link.symlink_to(path)
    with pytest.raises(CryptographicError):
        read_private(link)
    hard = root / "hard"
    os.link(path, hard)
    with pytest.raises(CryptographicError):
        read_private(hard)
    for candidate in (Path("/"), Path("relative"), root / "missing"):
        with pytest.raises(CryptographicError):
            read_private(candidate)


def test_parent_modes_and_nontext_material_rejected(tmp_path):
    root = tmp_path.resolve()
    root.chmod(0o755)
    with pytest.raises(CryptographicError):
        write_private(root / "key", b"synthetic")
    root.chmod(0o700)
    with pytest.raises(CryptographicError):
        write_private(root / "key", b"\xff")


def test_private_and_public_documents_cannot_cross_purposes():
    private = TokenPrivateKeyring([Key("token", "active", b"t" * 32)])
    public = private.public()
    for ring in (private, public):
        assert (
            parse_keyring(serialize_keyring(ring), ring.kind).inventory()
            == ring.inventory()
        )
    with pytest.raises(CryptographicError):
        parse_keyring(serialize_keyring(private), "token_public")
    assert not hasattr(
        parse_keyring(serialize_keyring(public), "token_public"), "decrypt"
    )


@pytest.mark.parametrize(
    "raw", [b"", b"[]", b"{", b'{"kind":1,"kind":2}', b"x" * (128 * 1024 + 1)]
)
def test_keyring_input_failure_never_echoes_private_values(raw):
    with pytest.raises(CryptographicError) as error:
        parse_keyring(raw, "general_encryption")
    assert "kind" not in str(error.value)


def test_duplicate_and_extra_key_fields_rejected():
    ring = GeneralKeyring([Key("g1", "active", b"g" * 32)])
    document = json.loads(serialize_keyring(ring))
    document["keys"][0]["private_comment"] = "synthetic-secret"
    with pytest.raises(CryptographicError):
        parse_keyring(json.dumps(document).encode(), "general_encryption")
