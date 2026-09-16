"""Private atomic export files; database owners decide admission and retention.

Only server UUIDs reach filenames. The owning worker keeps its campaign read
guard through rendering, then rechecks authority at publication; download and cleanup
owners likewise hold their response/drain guards. This store grants no access.
"""

import hashlib
import io
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from parishkit.config import ConfigError
from parishkit.stewardship.runtime_paths import private_directory

MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class ArtifactReceipt:
    """An opaque immutable output reference, not a user-supplied path or URL."""

    identifier: UUID
    size: int
    sha256: str

    def __post_init__(self):
        """Reject unbounded/ambiguous receipt values before touching storage."""
        _name(self.identifier)
        if (
            type(self.size) is not int
            or not 0 < self.size <= MAX_ARTIFACT_BYTES
            or type(self.sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None
        ):
            raise ValueError("Invalid export artifact receipt.")


def _name(identifier):
    """UUID hex has no traversal, extension, filesystem separator or private text."""
    if not isinstance(identifier, UUID):
        raise ValueError("Export storage requires a canonical UUID.")
    return identifier.hex


def _private(inode, *, directory=False, links=1):
    """Refuse public permissions, non-owner files, links and unexpected inode types."""
    predicate = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not predicate(inode.st_mode)
        or inode.st_uid != os.geteuid()
        or stat.S_IMODE(inode.st_mode) != (0o700 if directory else 0o600)
        or (not directory and inode.st_nlink != links)
    ):
        raise ConfigError("Export storage is not private.")


@contextmanager
def _directory(root, campaign_id, *, create=False):
    """Pin each descendant via dirfd so no symlink can redirect later operations."""
    name = _name(campaign_id)
    root = private_directory(root)
    descriptors = []
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(descriptor)
        _private(os.fstat(descriptor), directory=True)
        for component in ("exports", name):
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
            descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            descriptors.append(descriptor)
            _private(os.fstat(descriptor), directory=True)
        yield descriptor
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class _BoundedWriter(io.BufferedWriter):
    """Enforce file bounds during generation, including seek-based PDF writers."""

    def write(self, data):
        """Never let a renderer consume unbounded disk before a final size check."""
        if self.tell() + len(data) > MAX_ARTIFACT_BYTES:
            raise ConfigError("Export exceeds the artifact size limit.")
        return super().write(data)


def _digest(stream):
    """Hash bounded chunks, never load a large CSV/XLSX/PDF into memory at once."""
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(CHUNK_BYTES):
        size += len(chunk)
        if size > MAX_ARTIFACT_BYTES:
            raise ConfigError("Export exceeds the artifact size limit.")
        digest.update(chunk)
    return size, digest.hexdigest()


def write_artifact(root, campaign_id, identifier, render):
    """Publish complete bytes once, without overwriting any earlier attempt.

    A crash after link but before the SQL receipt can leave an unreferenced file.
    The owning job records its attempt UUID before calling this function, so its
    recovery/cleanup can remove that exact file. Never infer absence from an
    exception. Successful retries allocate a fresh attempt UUID.
    """
    name = _name(identifier)
    if not callable(render):
        raise TypeError("Export generation requires an internal renderer.")
    temporary = f".{name}.{uuid4().hex}.pending"
    try:
        with _directory(root, campaign_id, create=True) as directory:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode=0o600,
                dir_fd=directory,
            )
            try:
                with _BoundedWriter(
                    io.FileIO(descriptor, "wb", closefd=True)
                ) as stream:
                    _private(os.fstat(stream.fileno()))
                    render(stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                descriptor = os.open(
                    temporary, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory
                )
                with os.fdopen(descriptor, "rb") as stream:
                    _private(os.fstat(stream.fileno()))
                    size, digest = _digest(stream)
                receipt = ArtifactReceipt(identifier, size, digest)
                # link is atomic and fails if the immutable target already exists.
                os.link(
                    temporary,
                    name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
            finally:
                os.unlink(temporary, dir_fd=directory)
                os.fsync(directory)
            return receipt
    except OSError:
        raise ConfigError("Export storage is unavailable.") from None


@contextmanager
def open_artifact(root, campaign_id, receipt):
    """Verify retained bytes before yielding a stream inside the owner's read guard."""
    if not isinstance(receipt, ArtifactReceipt):
        raise TypeError("An immutable export receipt is required.")
    try:
        with _directory(root, campaign_id) as directory:
            descriptor = os.open(
                _name(receipt.identifier),
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            with os.fdopen(descriptor, "rb") as stream:
                inode = os.fstat(stream.fileno())
                _private(inode)
                if inode.st_size != receipt.size or _digest(stream) != (
                    receipt.size,
                    receipt.sha256,
                ):
                    raise ConfigError("Export file does not match its receipt.")
                stream.seek(0)
                yield stream
    except OSError:
        raise ConfigError("Export file is unavailable.") from None


class ArtifactChunks:
    """Closeable byte iterator, including disconnect before its first iteration."""

    def __init__(self, root, campaign_id, receipt):
        """Open and verify immediately while the response already owns its guard."""
        self.manager = open_artifact(root, campaign_id, receipt)
        self.stream = self.manager.__enter__()
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        """Read one bounded chunk; exhausted or failed iteration closes the inode."""
        if self.closed:
            raise StopIteration
        try:
            chunk = self.stream.read(CHUNK_BYTES)
            if not chunk:
                raise StopIteration
            return chunk
        except BaseException:
            self.close()
            raise

    def close(self):
        """Unlike an unstarted generator, close always releases the opened file."""
        if not self.closed:
            self.closed = True
            self.manager.__exit__(None, None, None)


def remove_attempt_artifacts(root, campaign_id, identifier):
    """Remove a drained attempt's final/pending files, including link-step crashes.

    Admission and exclusive campaign drainage belong to the cleanup worker.
    Inventory validation precedes deletion; a hardlink is accepted only for the
    exact target/pending pair this writer can leave, never an outside inode link.
    """
    name = _name(identifier)
    pending = re.compile(r"\." + name + r"\.[0-9a-f]{32}\.pending")
    try:
        with _directory(root, campaign_id) as directory:
            entries = {}
            with os.scandir(directory) as listing:
                for item in listing:
                    if item.name == name or pending.fullmatch(item.name):
                        entries[item.name] = item.stat(follow_symlinks=False)
                        if len(entries) > 2:
                            raise ConfigError("Export cleanup inventory is invalid.")
            for filename, inode in entries.items():
                twin = [
                    other
                    for other, metadata in entries.items()
                    if other != filename
                    and (metadata.st_dev, metadata.st_ino)
                    == (inode.st_dev, inode.st_ino)
                ]
                valid_pair = len(twin) == 1 and name in {filename, twin[0]}
                _private(inode, links=2 if valid_pair else 1)
            for filename in entries:
                os.unlink(filename, dir_fd=directory)
            os.fsync(directory)
            return len(entries)
    except FileNotFoundError:
        return 0
    except OSError:
        raise ConfigError("Export cleanup is unavailable.") from None
