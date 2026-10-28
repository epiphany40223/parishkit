"""The exact mounts the off-site uploader profile runs with, and nothing else.

The uploader (#235) copies finished, sealed backup sets to Google Drive, so
the backup profile, which reads every tree, need not reach the internet. Its
own inventory is deliberately small: a read-only root filesystem, its service
document and database password file, the startup interlock (read-only: the
uploader only ever holds it shared), the Google Workspace credential's
read-only rotating directory (mounted as mail-dispatch mounts it, so a key an
Administrator replaces is read on the next run), and the backups directory,
read-only. It mounts none of the configuration, credentials, media or
authority trees, no backup recipient key, no provisioning record and no
Valkey secret (operations specification, "Dedicated off-site uploader").
"""

import os
from pathlib import Path

from parishkit.config import ConfigError

from .deployment import DeploymentProfile, ServiceRole
from .local.clock import CLOCK_MOUNT_TARGET
from .runtime_paths import RuntimeLayout
from .service_boundaries import (
    _kernel_pseudo_mount,
    kernel_mounts,
    rotating_directories,
)

# The one credential the uploader reads: the Workspace key, for Drive.
UPLOADER_SECRETS = frozenset({"google_workspace"})


def uploader_targets(configuration):
    """Return destination/read-only pairs; every one of them is read-only.

    The Workspace key is mounted exactly as mail-dispatch mounts it: its
    rotating directory in the default layout, or the single file when its
    path is individually overridden (``rotating_directories``).
    """
    layout = RuntimeLayout(configuration).validate()
    if configuration.service_role is not ServiceRole.BACKUP_UPLOADER:
        raise ConfigError("The off-site uploader profile is required.")
    if (
        configuration.configuration_file is None
        or configuration.postgres.password_file is None
    ):
        raise ConfigError(
            "Uploader configuration and database file mounts are required."
        )
    if (
        configuration.postgres.download_password_file is not None
        or configuration.valkey.password_file is not None
    ):
        raise ConfigError("The uploader profile cannot reference online secrets.")
    if set(configuration.secrets) != UPLOADER_SECRETS:
        raise ConfigError("The uploader profile receives only the Workspace key.")
    key = rotating_directories(configuration).get(
        "google_workspace", configuration.secrets["google_workspace"]
    )
    backups = configuration.paths["backups"]
    targets = {}
    for path in (
        configuration.configuration_file,
        configuration.postgres.password_file,
        layout.interlock,
        key,
        backups,
    ):
        if path in targets:
            raise ConfigError("Uploader inputs cannot alias one path.")
        targets[path] = True
    # The backups directory is mounted whole, so nothing else may sit
    # inside it (and it may sit inside nothing else mounted here).
    for path in targets:
        if path != backups and (backups in path.parents or path in backups.parents):
            raise ConfigError("Uploader inputs overlap the backups directory.")
    return targets


def validate_uploader_mounts(configuration, mounts):
    """Admit exactly the uploader's inventory from kernel mount evidence.

    Every expected path must be mounted read-only, the root filesystem too,
    and any other mount is refused except Docker's own files, kernel pseudo
    filesystems, the development source tree and LOCAL's fake-clock mount,
    exactly as the backup profile's admission allows them.
    """
    expected = uploader_targets(configuration)
    actual = {mount.target: mount for mount in mounts}
    if len(actual) != len(mounts):
        raise ConfigError("Overmounted uploader paths are not admitted.")
    if Path("/") not in actual or not actual[Path("/")].read_only:
        raise ConfigError("Uploader application root filesystem must be read-only.")
    if any(path not in actual or not actual[path].read_only for path in expected):
        raise ConfigError("Uploader mount inventory is incomplete or writable.")
    for mount in mounts:
        if mount.target in {
            *expected,
            Path("/"),
            Path("/etc/hosts"),
            Path("/etc/hostname"),
            Path("/etc/resolv.conf"),
        }:
            continue
        if _kernel_pseudo_mount(mount):
            continue
        if (
            configuration.profile == "development"
            and mount.target == Path("/app/src")
            and mount.read_only
        ):
            continue
        if (
            configuration.profile is DeploymentProfile.LOCAL
            and mount.target == CLOCK_MOUNT_TARGET
            and mount.read_only
        ):
            # The fake-clock override's one mount (#476), LOCAL only.
            continue
        raise ConfigError("Uploader service has an unrelated mount.")
    return configuration.service_role


def admit_uploader_service(configuration):
    """The uploader uses its actual process UID and Linux mount evidence."""
    if os.geteuid() == 0:
        raise ConfigError("The uploader service cannot run as root.")
    return validate_uploader_mounts(configuration, kernel_mounts())


def admit_uploader_identity(database=None):
    """Admit the uploader's own login and exactly its grants before any Drive call.

    As every runtime login is admitted (``runtime_grants`` and
    ``accounts.credential_database``): the session is its own login with no
    superuser, membership, role creation or isolation drift; its table,
    column and definer authority is exactly the registry's (its outcome
    rows, three run columns, the runtime row's id, timeout log entries, the
    destination definer and the debug-off read); and it cannot create
    temporary objects. The ``upload`` command calls this; it is here so the
    login's admission ships, and is tested, with the login itself.
    """
    from django.db import connection

    from .accounts.credential_database import _identity, admit_grants
    from .runtime_database import require_no_temporary_authority
    from .runtime_grants import (
        admit_columns,
        login_name,
        runtime_functions,
        runtime_grants,
    )

    database = connection if database is None else database
    role = ServiceRole.BACKUP_UPLOADER
    _identity(login_name(role), database=database)
    tables, columns = runtime_grants(role)
    allowed = {table: set(grants) for table, grants in tables.items()}
    for table, grants in columns.items():
        allowed.setdefault(table, set()).update(grants)
    admit_grants(allowed, database=database, functions=runtime_functions(role))
    admit_columns(database, tables, columns)
    require_no_temporary_authority(database)
