"""Immutable normalized branding files under the deployment's durable media root.

This layer owns only exact server-UUID bundles, not authorization or SQL receipts.
An incomplete bundle is never served: the owning workflow records metadata only
after all files and their directory entries have been flushed durably.
"""

import fcntl
import hashlib
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import UUID, uuid5

from parishkit.config import ConfigError
from parishkit.stewardship.runtime_paths import private_directory
from parishkit.stewardship.web.content import Graphic

VARIANTS = {"large": 1024, "menu": 128, "icon": 128, "favicon": 32}
MAX_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class BrandingFile:
    """Safe immutable file identity and digest, without any submitted filename."""

    reference: UUID
    label: str
    width: int
    height: int
    size: int
    sha256: str


@contextmanager
def _directory(path):
    """Pin an admitted owner-only directory rather than repeatedly resolving paths."""
    path = private_directory(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise ConfigError("Branding directory is not private.")
        yield descriptor
    finally:
        os.close(descriptor)


def _identifier(value):
    """Only a server-generated UUID can identify a bundle, never a browser path."""
    if not isinstance(value, UUID):
        raise ValueError("Branding bundle identity must be a UUID.")
    return value.hex


@contextmanager
def media_lock(media_root):
    """Serialize this store's short upload/cleanup windows without waiting in web.

    The owner takes this before SQL lifecycle checks. A cleanup checkpoint cannot
    race ahead of a late writer and leave files behind after claiming scrubbing.
    All actors must use this fixed inode; it is never removed by bundle cleanup.
    """
    root = private_directory(media_root)
    directory = private_directory(root / "branding", create=True)
    with _directory(directory) as parent:
        descriptor = os.open(
            ".lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            mode=0o600,
            dir_fd=parent,
        )
        try:
            inode = os.fstat(descriptor)
            if (
                not stat.S_ISREG(inode.st_mode)
                or inode.st_uid != os.geteuid()
                or stat.S_IMODE(inode.st_mode) != 0o600
                or inode.st_nlink != 1
            ):
                raise ConfigError("Branding lock is not private.")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ConfigError("Branding storage is busy; retry shortly.") from None
            yield
        finally:
            os.close(descriptor)


def _inventory(bundle_id, graphics):
    """Freeze metadata from the existing normalizer's three bounded PNG variants."""
    _identifier(bundle_id)
    if type(graphics) is not dict or set(graphics) != {"large", "small", "favicon"}:
        raise ValueError("A complete normalized branding bundle is required.")
    result = []
    for label, maximum in VARIANTS.items():
        graphic = graphics["small" if label in {"menu", "icon"} else label]
        if (
            not isinstance(graphic, Graphic)
            or graphic.content_type != "image/png"
            or type(graphic.data) is not bytes
            or not 0 < len(graphic.data) <= MAX_BYTES
            or not graphic.data.startswith(b"\x89PNG\r\n\x1a\n")
            or type(graphic.width) is not int
            or not 0 < graphic.width <= maximum
            or type(graphic.height) is not int
            or not 0 < graphic.height <= maximum
        ):
            raise ValueError("Branding variants must be normalized bounded PNGs.")
        result.append(
            (
                BrandingFile(
                    uuid5(bundle_id, label),
                    label,
                    graphic.width,
                    graphic.height,
                    len(graphic.data),
                    hashlib.sha256(graphic.data).hexdigest(),
                ),
                graphic.data,
            )
        )
    return tuple(result)


def create_bundle(media_root, bundle_id, graphics):
    """Create once with exclusive files; never overwrite an earlier upload on retry.

    The caller owns cleanup after any exception, including a failed fsync. It
    must not infer that nothing was created merely because no receipt returned.
    Only normalized output is written; the original upload is never retained.
    """
    inventory = _inventory(bundle_id, graphics)
    try:
        root = private_directory(media_root)
        directory = root / "branding"
        private_directory(directory, create=True)
        with _directory(root) as descriptor:
            os.fsync(descriptor)
        with _directory(directory) as parent:
            os.mkdir(_identifier(bundle_id), mode=0o700, dir_fd=parent)
            os.fsync(parent)
        with _directory(directory / bundle_id.hex) as descriptor:
            for metadata, data in inventory:
                file = os.open(
                    metadata.label + ".png",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    mode=0o600,
                    dir_fd=descriptor,
                )
                with os.fdopen(file, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            os.fsync(descriptor)
    except OSError:
        raise ConfigError("Branding storage is unavailable.") from None
    return tuple(metadata for metadata, _ in inventory)


def read_variant(media_root, bundle_id, metadata):
    """Serve only bytes matching the durable receipt; reject links and changed files."""
    _identifier(bundle_id)
    if (
        not isinstance(metadata, BrandingFile)
        or metadata.label not in VARIANTS
        or metadata.reference != uuid5(bundle_id, metadata.label)
        or type(metadata.size) is not int
        or not 0 < metadata.size <= MAX_BYTES
    ):
        raise ValueError("Invalid branding file receipt.")
    try:
        root = private_directory(media_root)
        with _directory(root / "branding" / bundle_id.hex) as directory:
            descriptor = os.open(
                metadata.label + ".png",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            with os.fdopen(descriptor, "rb") as stream:
                inode = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(inode.st_mode)
                    or inode.st_uid != os.geteuid()
                    or stat.S_IMODE(inode.st_mode) != 0o600
                    or inode.st_nlink != 1
                    or inode.st_size != metadata.size
                ):
                    raise ConfigError("Branding file does not match its receipt.")
                data = stream.read(MAX_BYTES + 1)
        if (
            len(data) != metadata.size
            or hashlib.sha256(data).hexdigest() != metadata.sha256
        ):
            raise ConfigError("Branding file does not match its receipt.")
        return data
    except OSError:
        raise ConfigError("Branding file is unavailable.") from None


def remove_bundle(media_root, bundle_id):
    """Idempotently remove only a locked, unreferenced bundle approved by its owner.

    The caller must hold its lifecycle lock and first rule out retained YAML
    references. This private store has no authority to decide retention. Refuse
    unexpected entries, links and directories before deleting any of the files.
    """
    name = _identifier(bundle_id)
    root = private_directory(media_root)
    directory = root / "branding"
    if not directory.exists() and not directory.is_symlink():
        return
    try:
        with _directory(directory) as parent:
            try:
                descriptor = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                )
            except FileNotFoundError:
                return
            try:
                inode = os.fstat(descriptor)
                if inode.st_uid != os.geteuid() or stat.S_IMODE(inode.st_mode) != 0o700:
                    raise ConfigError("Branding directory is not private.")
                names = os.listdir(descriptor)
                if set(names) - {label + ".png" for label in VARIANTS}:
                    raise ConfigError("Branding bundle contains unexpected files.")
                for filename in names:
                    inode = os.stat(filename, dir_fd=descriptor, follow_symlinks=False)
                    if (
                        not stat.S_ISREG(inode.st_mode)
                        or inode.st_uid != os.geteuid()
                        or stat.S_IMODE(inode.st_mode) != 0o600
                        or inode.st_nlink != 1
                        or inode.st_size > MAX_BYTES
                    ):
                        raise ConfigError("Branding cleanup inventory is invalid.")
                for filename in names:
                    os.unlink(filename, dir_fd=descriptor)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.rmdir(name, dir_fd=parent)
            os.fsync(parent)
    except OSError:
        raise ConfigError("Branding cleanup is unavailable.") from None
