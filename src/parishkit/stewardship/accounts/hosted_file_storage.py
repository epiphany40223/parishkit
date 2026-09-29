"""Hosted-file bytes (#346) under the deployment's durable media root.

Each file is one regular owner-only file, ``<media>/hosted-files/<id hex>``:
no extension and no name from the upload. The database row is the receipt;
this layer never decides whether a file may be written or removed. Writers
and removers hold ``storage_lock`` from before the disk step until their
database transaction ends, so the orphan sweep never races an upload.
"""

import fcntl
import os
import re
import stat
from contextlib import contextmanager
from uuid import UUID

from parishkit.config import ConfigError
from parishkit.stewardship.runtime_paths import private_directory

DIRECTORY = "hosted-files"
_NAME = re.compile(r"[0-9a-f]{32}")


def _name(file_id):
    """Only a server-generated UUID names a stored file."""
    if not isinstance(file_id, UUID):
        raise ValueError("A hosted file is named by its UUID.")
    return file_id.hex


def _directory(media_root, *, create=False):
    """The private hosted-files directory, created on first use."""
    return private_directory(private_directory(media_root) / DIRECTORY, create=create)


@contextmanager
def _open_directory(media_root, *, create=False):
    """Pin the directory by descriptor rather than resolving paths repeatedly."""
    descriptor = os.open(
        _directory(media_root, create=create),
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _regular(metadata, size=None):
    """Whether an inode is a private single-link regular file of ``size``."""
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1
        and (size is None or metadata.st_size == size)
    )


@contextmanager
def storage_lock(media_root):
    """Serialize uploads, deletions and the orphan sweep without waiting.

    A busy lock is a retryable refusal: web requests never queue on it.
    """
    try:
        with _open_directory(media_root, create=True) as parent:
            descriptor = os.open(
                ".lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                mode=0o600,
                dir_fd=parent,
            )
            try:
                if not _regular(os.fstat(descriptor)):
                    raise ConfigError("Hosted file lock is not private.")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ConfigError("Hosted files are busy; retry shortly.") from None
                yield
            finally:
                os.close(descriptor)
    except OSError:
        raise ConfigError("Hosted file storage is unavailable.") from None


def write(media_root, file_id, data):
    """Create the file once, durably; never overwrite an existing one."""
    try:
        with _open_directory(media_root, create=True) as parent:
            descriptor = os.open(
                _name(file_id),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                mode=0o600,
                dir_fd=parent,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(parent)
    except OSError:
        raise ConfigError("Hosted file storage is unavailable.") from None


def open_file(media_root, file_id, size):
    """Open a stored file for streaming, or None if it is missing or altered.

    The caller closes the returned binary stream. A link, a changed size or
    unexpected ownership reads as missing, never as another file's bytes.
    """
    try:
        with _open_directory(media_root) as parent:
            descriptor = os.open(
                _name(file_id),
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
    except (OSError, ConfigError):
        return None
    stream = os.fdopen(descriptor, "rb")
    if not _regular(os.fstat(stream.fileno()), size):
        stream.close()
        return None
    return stream


def exists(media_root, file_id, size):
    """Whether the stored file is present and matches its recorded size."""
    stream = open_file(media_root, file_id, size)
    if stream is None:
        return False
    stream.close()
    return True


def remove(media_root, file_id):
    """Idempotently remove one stored file; refuse anything but a regular file."""
    try:
        with _open_directory(media_root, create=True) as parent:
            try:
                metadata = os.stat(_name(file_id), dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return
            if not stat.S_ISREG(metadata.st_mode):
                raise ConfigError("Hosted file storage holds an unexpected entry.")
            os.unlink(_name(file_id), dir_fd=parent)
            os.fsync(parent)
    except OSError:
        raise ConfigError("Hosted file storage is unavailable.") from None


def sweep(media_root, keep):
    """Remove stored files that no row names (a crash between disk and commit).

    ``keep`` is the set of every row's UUID. Only UUID-named regular files
    are removed; the lock file and anything unexpected are left alone.
    """
    keep = {_name(value) for value in keep}
    try:
        with _open_directory(media_root, create=True) as parent:
            for name in os.listdir(parent):
                if not _NAME.fullmatch(name) or name in keep:
                    continue
                metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISREG(metadata.st_mode):
                    os.unlink(name, dir_fd=parent)
            os.fsync(parent)
    except OSError:
        raise ConfigError("Hosted file storage is unavailable.") from None
