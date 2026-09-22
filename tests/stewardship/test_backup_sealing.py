"""A sealed backup opens only with its key, whole and unchanged."""

import io

import pytest
from nacl.public import PrivateKey

from parishkit.stewardship import backup_sealing as sealing


@pytest.fixture
def keys(tmp_path):
    """A generated pair written the way the operator's files are."""
    private, public = sealing.generate_keypair()
    (tmp_path / "private").write_text(private)
    (tmp_path / "public").write_text(public)
    return sealing.load_private(tmp_path / "private"), sealing.Recipient.load(
        tmp_path / "public"
    )


def roundtrip(plaintext, private, recipient, *, tamper=None):
    """Seal, optionally tamper, open; return (sealed, opened, count, digest)."""
    sealed = io.BytesIO()
    count, digest = sealing.seal(
        io.BytesIO(plaintext), sealed, recipient=recipient, kind="database"
    )
    assert count == len(plaintext)
    data = bytearray(sealed.getvalue())
    if tamper is not None:
        data = tamper(data)
    opened = io.BytesIO()
    result = sealing.open_sealed(io.BytesIO(bytes(data)), opened, private=private)
    return result, opened.getvalue(), count, digest


@pytest.mark.parametrize(
    "size",
    [0, 1, sealing.CHUNK - 1, sealing.CHUNK, sealing.CHUNK + 1, 3 * sealing.CHUNK],
)
def test_every_chunk_boundary_roundtrips(keys, size):
    """Empty, short, exact and multi-chunk inputs come back byte for byte."""
    private, recipient = keys
    plaintext = bytes(range(256)) * (size // 256) + bytes(size % 256)
    (kind, count, digest), opened, sealed_count, sealed_digest = roundtrip(
        plaintext, private, recipient
    )
    assert opened == plaintext
    assert (kind, count, digest) == ("database", sealed_count, sealed_digest)


def test_the_header_names_the_recipient_and_the_kind(keys, tmp_path):
    """A wrong key is refused by name before any chunk is touched."""
    private, recipient = keys
    sealed = io.BytesIO()
    sealing.seal(io.BytesIO(b"x"), sealed, recipient=recipient, kind="files")
    assert sealed.getvalue().startswith(sealing.MAGIC)
    header = sealed.getvalue().split(b"\n")[1]
    assert recipient.fingerprint.encode() in header and b'"kind": "files"' in header
    with pytest.raises(sealing.SealError, match="different key"):
        sealing.open_sealed(
            io.BytesIO(sealed.getvalue()), io.BytesIO(), private=PrivateKey.generate()
        )
    assert sealing.fingerprint(private.public_key) == recipient.fingerprint
    assert len(recipient.fingerprint) == 16


def body_start(data):
    """Where the first frame begins: after the magic and the header line."""
    return data.index(b"\n", len(sealing.MAGIC)) + 1


@pytest.mark.parametrize(
    "tamper, message",
    [
        (lambda d: d[: body_start(d) + 10], "truncated"),
        (lambda d: d[:-1], "authentication"),
        (lambda d: d + b"x", "authentication"),
        (lambda d: d[:-5] + bytes([d[-5] ^ 1]) + d[-4:], "authentication"),
        (lambda d: d[: len(sealing.MAGIC)] + b"{}\n" + d[body_start(d) :], "malformed"),
        (lambda d: b"NOPE" + d[4:], "not a sealed"),
    ],
)
def test_a_changed_sealed_backup_is_refused(keys, tamper, message):
    """Truncation, appended data, a flipped byte and a broken header all refuse."""
    private, recipient = keys
    with pytest.raises(sealing.SealError, match=message):
        roundtrip(b"payload" * 1000, private, recipient, tamper=tamper)


def test_chunks_cannot_be_reordered_or_repeated(keys):
    """Positions are the nonces: swapping two full chunks fails authentication."""
    private, recipient = keys
    plaintext = b"a" * sealing.CHUNK + b"b" * sealing.CHUNK + b"c"
    sealed = io.BytesIO()
    sealing.seal(io.BytesIO(plaintext), sealed, recipient=recipient, kind="database")
    data = sealed.getvalue()
    start = body_start(data)
    first = data[start : start + sealing.FRAME]
    second = data[start + sealing.FRAME : start + 2 * sealing.FRAME]
    swapped = data[:start] + second + first + data[start + 2 * sealing.FRAME :]
    with pytest.raises(sealing.SealError, match="authentication"):
        sealing.open_sealed(io.BytesIO(swapped), io.BytesIO(), private=private)


def test_key_files_are_validated(tmp_path):
    """Anything but one base64 key of the right size is refused."""
    (tmp_path / "bad").write_text("not base64!\n")
    (tmp_path / "short").write_text("YWJj\n")
    for name in ("bad", "short", "missing"):
        with pytest.raises(sealing.SealError):
            sealing.load_private(tmp_path / name)
        with pytest.raises(sealing.SealError):
            sealing.Recipient.load(tmp_path / name)
    with pytest.raises(TypeError):
        sealing.seal(io.BytesIO(b""), io.BytesIO(), recipient=object(), kind="database")
    private, public = sealing.generate_keypair()
    (tmp_path / "public").write_text(public)
    with pytest.raises(TypeError):
        sealing.seal(
            io.BytesIO(b""),
            io.BytesIO(),
            recipient=sealing.Recipient.load(tmp_path / "public"),
            kind="other",
        )
