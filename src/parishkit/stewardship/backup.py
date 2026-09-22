"""The v1 backup: a sealed database dump and a sealed copy of the runtime's files.

One run writes a dated directory under the backups path holding the
PostgreSQL custom-format dump and a tar of the configuration and credentials
trees, each sealed to the human-held recipient key, and a plaintext manifest
naming sizes, digests, durations and the key, never contents. Only a completed
run records a row; the scheduler reads the newest row to alert when a backup
is overdue, and the offline upgrade commands read it as the verified-backup
evidence a configured deployment requires. Copying the directory off the host
is the operator's cron job, as the backup runbook says. The reduced scope,
and what it defers, is the v1 launch scope's.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from parishkit import __version__
from parishkit.config import ConfigError

from .accounts.authority import _sync_directory
from .accounts.key_files import read_private
from .backup_sealing import Recipient, seal
from .observability import Event, FailureKind, emit
from .runtime_paths import (
    PROVISIONING_RECORD,
    RuntimeLayout,
    explicit_path,
    private_directory,
)

# The specification's window: a successful backup is required every 24 hours,
# and the offline upgrade commands accept one no older than that.
REQUIRED_WITHIN = timedelta(hours=24)
# Complete sets kept on the host; the off-host copy is the operator's.
RETAINED_SETS = 30
# The archived trees are small (branding images are at most a few megabytes
# each); anything larger is not what this backup was designed for and stops
# before sealing.
MAX_FILES_BYTES = 256 * 1024 * 1024
SET_NAME = re.compile(r"^\d{8}T\d{6}Z$")
DUMP = "database.pgdump.sealed"
FILES = "files.tar.sealed"
MANIFEST = "manifest.json"


def _password(path):
    """Keep the SQL password in memory only, for the dump process environment."""
    value = read_private(path).removesuffix(b"\n")
    if not value or any(byte <= 32 or byte >= 127 for byte in value):
        raise ConfigError("The backup database password file is invalid.")
    return value.decode("ascii")


def _output_file(directory, name):
    """Create one owner-only output file that must not already exist."""
    descriptor = os.open(
        directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    return os.fdopen(descriptor, "wb")


def _finish(stream):
    """Make a completed output durable before the manifest names it."""
    stream.flush()
    os.fsync(stream.fileno())
    stream.close()


# Everything a replacement host needs beside the database: the authority and
# rendered documents, every credential, and the uploaded branding the restored
# database refers to.
ARCHIVED_TREES = ("config", "credentials", "media")


def _directory(archive, name, metadata):
    """Record one owner-only directory, so extraction never widens its mode."""
    entry = tarfile.TarInfo(str(name))
    entry.type, entry.mode, entry.mtime = tarfile.DIRTYPE, 0o700, int(metadata.st_mtime)
    archive.addfile(entry)


def _file(archive, name, path, *, budget=MAX_FILES_BYTES):
    """Record one regular file from a single open descriptor; return its size.

    Opening first and then reading exactly the descriptor's size keeps a
    file replaced by rename (how branding is written) consistent, and refuses
    a symlink or special file rather than following it. The size bound is
    checked before any content is read.
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigError("The runtime trees contain a non-regular file.")
        if metadata.st_size > budget:
            raise ConfigError("The runtime trees exceed the backup bound.")
        entry = tarfile.TarInfo(str(name))
        entry.size, entry.mode, entry.mtime = (
            metadata.st_size,
            0o600,
            int(metadata.st_mtime),
        )
        archive.addfile(entry, stream)
    return metadata.st_size


def archive_files(configuration, sink):
    """Tar the archived trees and the provisioning record, regular files only.

    Symlinks, devices and anything else are refused rather than followed or
    skipped silently: a tree that contains one is not the tree provisioning
    made. Members are named by tree, not by host path, so the restore moves
    each tree to wherever the deployment configures it. Online services write
    media while this runs, so a media file or directory that disappears
    before it is read is left out rather than failing the night's backup.
    """
    total = 0
    with tarfile.open(fileobj=sink, mode="w", format=tarfile.PAX_FORMAT) as archive:
        record = RuntimeLayout(configuration).provisioning_record
        total += _file(archive, PROVISIONING_RECORD, record)
        # Each file is bounded by what remains, before its content is read.
        for name in ARCHIVED_TREES:
            root = configuration.paths[name]
            _directory(archive, name, root.lstat())
            for path in sorted(root.rglob("*")):
                relative = Path(name) / path.relative_to(root)
                try:
                    metadata = path.lstat()
                    if stat.S_ISDIR(metadata.st_mode):
                        _directory(archive, relative, metadata)
                        continue
                    if not stat.S_ISREG(metadata.st_mode):
                        raise ConfigError(
                            "The runtime trees contain a non-regular file."
                        )
                    total += _file(
                        archive, relative, path, budget=MAX_FILES_BYTES - total
                    )
                except FileNotFoundError:
                    if name != "media":
                        raise
                    continue
    return total


def dump_database(configuration, sink, *, recipient):
    """Run pg_dump into the sealer; return the plaintext size and digest.

    The password reaches pg_dump through its environment, never its arguments.
    A failed dump leaves the sealed output unusable and is reported as one
    generic refusal plus the ``backup_dump_failed`` failure category in the
    process log; pg_dump's own stderr is drained and discarded.
    """
    binary = shutil.which("pg_dump")
    if binary is None:
        raise ConfigError("pg_dump is not installed in this image.")
    db = configuration.postgres
    # Owners and privileges are kept: every definer function's REVOKE from
    # PUBLIC and every runtime grant live only in the ACLs, so a dump without
    # them restores a database the services' own admission refuses. The same
    # role names exist wherever the deployment's roles were provisioned.
    command = [
        binary,
        "--format=custom",
        "--host",
        db.host,
        "--port",
        str(db.port),
        "--username",
        db.user,
        "--dbname",
        db.name,
    ]
    environment = {
        "PGPASSWORD": _password(db.password_file),
        "PGCONNECT_TIMEOUT": str(db.connect_timeout),
        "PGSSLMODE": "disable",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }

    def drain(stream):
        """Read and discard pg_dump's diagnostics, so a chatty dump cannot stall.

        Its text can name hosts, roles and paths, and the log formatter drops
        free text anyway; the failure is reported by a reviewed event instead.
        """
        while stream.read(65536):
            pass

    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment
    ) as process:
        reader = threading.Thread(target=drain, args=(process.stderr,), daemon=True)
        reader.start()
        count, digest = seal(process.stdout, sink, recipient=recipient, kind="database")
        process.wait(timeout=3600)
        reader.join(timeout=30)
    if process.returncode != 0 or count == 0:
        # Tell the operator the database step failed, as distinct from the
        # configuration refusals the backup command reports by category.
        emit(
            Event.STARTUP_REJECTED,
            level=logging.ERROR,
            failure_kind=FailureKind.BACKUP_DUMP,
        )
        raise ConfigError("The database dump did not complete.")
    return count, digest


def _prune(backups):
    """Keep the newest retained complete sets; count and remove only those.

    A failed run's directory has no manifest: it is left for inspection and
    never counts toward retention, so a run of failures cannot evict good
    sets. The operator removes failed directories by hand.
    """
    sets = sorted(
        path
        for path in backups.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and SET_NAME.match(path.name)
        and (path / MANIFEST).is_file()
    )
    for path in sets[:-RETAINED_SETS]:
        shutil.rmtree(path)


def run_backup(configuration, *, record):
    """Write one sealed backup set and record it; return the manifest.

    `record` persists the completed run's facts (the caller owns the database
    session) and runs only after every output is durable, so a row never
    names a set that does not exist. Retention runs after the record.
    """
    layout = RuntimeLayout(configuration)
    # An authority moved outside the archived trees would be silently left out
    # of every set. Refuse here, at run time, not when topologies render: the
    # override stays a supported deployment, it just cannot be backed up.
    if not any(
        configuration.paths[name] == configuration.paths["authority"]
        or configuration.paths[name] in configuration.paths["authority"].parents
        for name in ARCHIVED_TREES
    ):
        raise ConfigError("The authority store must live inside an archived tree.")
    recipient = Recipient.load(layout.credential("backup_data"))
    backups = private_directory(explicit_path(configuration.paths["backups"]))
    started = datetime.now(UTC)
    directory = private_directory(
        backups / started.strftime("%Y%m%dT%H%M%SZ"), create=True
    )
    if any(directory.iterdir()):
        raise ConfigError("A backup set with this name already exists.")
    clock = time.monotonic()
    with _output_file(directory, DUMP) as sink:
        database_bytes, database_digest = dump_database(
            configuration, sink, recipient=recipient
        )
        _finish(sink)
    database_seconds = round(time.monotonic() - clock, 3)
    clock = time.monotonic()
    with tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024) as spool:
        archive_files(configuration, spool)
        spool.seek(0)
        with _output_file(directory, FILES) as sink:
            files_bytes, files_digest = seal(
                spool, sink, recipient=recipient, kind="files"
            )
            _finish(sink)
    files_seconds = round(time.monotonic() - clock, 3)
    manifest = {
        "version": 1,
        "application_version": __version__,
        "started_at": started.isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "recipient_fingerprint": recipient.fingerprint,
        "database": {
            "file": DUMP,
            "plaintext_bytes": database_bytes,
            "plaintext_sha256": database_digest,
            "sealed_sha256": _sha256(directory / DUMP),
            "seconds": database_seconds,
        },
        "files": {
            "file": FILES,
            "plaintext_bytes": files_bytes,
            "plaintext_sha256": files_digest,
            "sealed_sha256": _sha256(directory / FILES),
            "seconds": files_seconds,
        },
    }
    encoded = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
    with _output_file(directory, MANIFEST) as sink:
        sink.write(encoded)
        _finish(sink)
    _sync_directory(directory)
    _sync_directory(backups)
    record(
        started_at=started,
        database_bytes=database_bytes,
        files_bytes=files_bytes,
        manifest_digest=hashlib.sha256(encoded).hexdigest(),
        recipient_fingerprint=recipient.fingerprint,
        application_version=__version__,
    )
    _prune(backups)
    return manifest


def _sha256(path):
    """Digest one sealed output for the manifest."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class RecentBackupRequired(ConfigError):
    """A configured deployment asked to change without a backup in the window.

    The operator commands report every refusal with one generic line; this
    one also logs the ``upgrade_backup_required`` failure category, since the
    remedy is always the same.
    """


def require_recent_backup(cursor):
    """Refuse a configured change unless a backup is recorded in the window."""
    if not recent_backup_recorded(cursor):
        raise RecentBackupRequired(
            "Configured upgrades require upgrade admission: a backup recorded "
            "within 24 hours."
        )


def recent_backup_recorded(cursor):
    """True when a completed backup is recorded within the required window.

    The offline upgrade commands call this on a configured deployment before
    changing its schema or grants: the row is the evidence, the clock is the
    database's, and a missing table (a database older than this feature) is
    the same as no backup.
    """
    cursor.execute("SELECT to_regclass('public.stewardship_backup_run')")
    if cursor.fetchone()[0] is None:
        return False
    cursor.execute(
        "SELECT EXISTS(SELECT 1 FROM public.stewardship_backup_run "
        "WHERE completed_at>=clock_timestamp()-%s)",
        [REQUIRED_WITHIN],
    )
    return cursor.fetchone()[0]
