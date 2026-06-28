"""Filesystem helpers for credential and cache files."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: str | Path, text: str, *, mode: int = 0o600) -> None:
    """Atomically write text to a file with restrictive permissions.

    Writes to a temporary file in the same directory, fsyncs it, then renames
    it over the target so readers never observe a partially written file. The
    temp file is created with ``mode`` (default owner-only ``0o600``) so the
    secret is never briefly world-readable. On any failure the descriptor is
    closed and the temp file removed before the error propagates.
    """

    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(text)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, target)
        target.chmod(mode)
    except Exception:
        with contextlib.suppress(OSError):
            os.close(fd)
        temp_path.unlink(missing_ok=True)
        raise
