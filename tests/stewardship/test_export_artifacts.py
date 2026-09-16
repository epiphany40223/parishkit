"""Real filesystem permissions, publication failure and bounded streaming checks."""

import hashlib
import os
from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.reports import artifacts
from parishkit.stewardship.reports.artifacts import (
    ArtifactChunks,
    ArtifactReceipt,
    open_artifact,
    remove_artifact,
    remove_attempt_artifacts,
    write_artifact,
)


@pytest.fixture
def store(tmp_path):
    """Tests own an isolated private root; no retained runtime data is touched."""
    root = tmp_path / "reports"
    root.mkdir(mode=0o700)
    return root, uuid4(), uuid4()


def location(store):
    """Resolve the expected test-owned output, not the production path contract."""
    root, campaign, identifier = store
    return root / "exports" / campaign.hex / identifier.hex


def test_complete_artifact_is_atomic_private_and_streamed(store):
    """Only complete durable bytes are published; file names contain no PII."""
    root, campaign, identifier = store
    payload = b"a,b\r\n" * 100000

    def render(stream):
        """Assert the target remains absent throughout partial output generation."""
        stream.write(payload[:100])
        assert not location(store).exists()
        stream.write(payload[100:])

    receipt = write_artifact(*store, render)
    assert receipt == ArtifactReceipt(
        identifier, len(payload), hashlib.sha256(payload).hexdigest()
    )
    assert location(store).stat().st_mode & 0o777 == 0o600
    assert location(store).parent.stat().st_mode & 0o777 == 0o700
    assert list(location(store).parent.iterdir()) == [location(store)]
    with open_artifact(root, campaign, receipt) as stream:
        assert stream.read(9) == payload[:9]
        assert stream.read() == payload[9:]
    assert stream.closed
    assert remove_artifact(*store)
    assert not remove_artifact(*store)


def test_render_failure_and_collision_do_not_publish_or_replace(store):
    """Failure cleanup removes only this attempt's temporary file."""

    def fail(stream):
        """Simulate a renderer failure after writing incomplete private content."""
        stream.write(b"partial")
        raise RuntimeError("synthetic renderer failure")

    with pytest.raises(RuntimeError):
        write_artifact(*store, fail)
    assert list(location(store).parent.iterdir()) == []
    write_artifact(*store, lambda stream: stream.write(b"first"))
    with pytest.raises(ConfigError):
        write_artifact(*store, lambda stream: stream.write(b"second"))
    assert location(store).read_bytes() == b"first"
    assert list(location(store).parent.iterdir()) == [location(store)]


def test_size_bound_is_enforced_during_writes(store, monkeypatch):
    """A large renderer cannot fill disk before receipt validation happens."""
    monkeypatch.setattr(artifacts, "MAX_ARTIFACT_BYTES", 10)
    with pytest.raises(ValueError):
        write_artifact(*store, lambda stream: stream.write(b"x" * 11))
    assert not location(store).exists()
    with pytest.raises(ValueError):
        write_artifact(*store, lambda stream: None)
    assert not location(store).exists()


def test_corruption_wrong_receipt_and_public_mode_fail_before_read(store):
    """Never yield bytes from a substituted or less-private artifact."""
    root, campaign, _ = store
    receipt = write_artifact(*store, lambda stream: stream.write(b"original"))
    with (
        pytest.raises(ConfigError),
        open_artifact(root, campaign, replace(receipt, size=1)),
    ):
        pytest.fail("incorrect receipt was admitted")
    location(store).write_bytes(b"tampered")
    with pytest.raises(ConfigError), open_artifact(root, campaign, receipt):
        pytest.fail("corrupt file was admitted")
    location(store).chmod(0o644)
    with pytest.raises(ConfigError), open_artifact(root, campaign, receipt):
        pytest.fail("public file was admitted")
    with pytest.raises(ConfigError):
        remove_artifact(*store)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "fifo"])
def test_unsafe_inode_is_neither_read_nor_deleted(store, tmp_path, kind):
    """Symlinks/FIFOs cannot redirect or hang authorized reading and cleanup."""
    root, campaign, identifier = store
    location(store).parent.mkdir(parents=True, mode=0o700)
    (root / "exports").chmod(0o700)
    other = tmp_path / "other"
    other.write_bytes(b"private")
    other.chmod(0o600)
    if kind == "symlink":
        location(store).symlink_to(other)
    elif kind == "hardlink":
        os.link(other, location(store))
    elif kind == "directory":
        location(store).mkdir(mode=0o700)
    else:
        os.mkfifo(location(store), mode=0o600)
    receipt = ArtifactReceipt(identifier, 7, hashlib.sha256(b"private").hexdigest())
    with pytest.raises(ConfigError), open_artifact(root, campaign, receipt):
        pytest.fail("unsafe file was admitted")
    with pytest.raises(ConfigError):
        remove_artifact(*store)
    assert other.read_bytes() == b"private"


def test_symlink_directory_is_not_followed(store, tmp_path):
    """Every descendant is opened relative to a pinned no-follow directory."""
    root, campaign, _ = store
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (root / "exports").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ConfigError):
        write_artifact(*store, lambda stream: stream.write(b"data"))
    assert not (outside / campaign.hex).exists()


def test_missing_cleanup_and_invalid_public_inputs(store):
    """Missing exact artifacts are harmless; path strings never reach syscalls."""
    root, campaign, identifier = store
    assert not remove_artifact(*store)
    with pytest.raises(ValueError):
        write_artifact(root, campaign, "../secret", lambda stream: None)
    with pytest.raises(TypeError):
        write_artifact(*store, None)
    with pytest.raises(TypeError), open_artifact(root, campaign, None):
        pass
    with pytest.raises(ValueError):
        ArtifactReceipt(identifier, True, "x" * 64)
    receipt = ArtifactReceipt(identifier, 1, "0" * 64)
    with pytest.raises(ConfigError), open_artifact(root, campaign, receipt):
        pass


def test_stream_closes_without_first_iteration_and_at_exhaustion(store):
    """WSGI disconnect can happen before a generator would enter its finally block."""
    root, campaign, _ = store
    receipt = write_artifact(*store, lambda stream: stream.write(b"data"))
    chunks = ArtifactChunks(root, campaign, receipt)
    chunks.close()
    chunks.close()
    assert chunks.stream.closed
    assert list(chunks) == []
    chunks = ArtifactChunks(root, campaign, receipt)
    assert list(chunks) == [b"data"]
    assert chunks.stream.closed


def test_cleanup_handles_exact_link_crash_pair_and_preserves_other_files(store):
    """A crash between link and unlink leaves a provable two-name private inode."""
    write_artifact(*store, lambda stream: stream.write(b"data"))
    target = location(store)
    pending = target.with_name(f".{store[2].hex}.{uuid4().hex}.pending")
    os.link(target, pending)
    other = target.with_name(uuid4().hex)
    other.write_bytes(b"other attempt")
    assert remove_attempt_artifacts(*store) == 2
    assert list(target.parent.iterdir()) == [other]
    assert remove_attempt_artifacts(*store) == 0


def test_attempt_cleanup_rejects_outside_links_and_unexpected_inventory(
    store, tmp_path
):
    """Even expired-file cleanup must not follow links or broaden its UUID scope."""
    write_artifact(*store, lambda stream: stream.write(b"data"))
    target = location(store)
    os.link(target, tmp_path / "outside-link")
    with pytest.raises(ConfigError):
        remove_attempt_artifacts(*store)
    assert target.exists()
