"""The exact mounts the backup profile runs with, and nothing else.

The backup reads the whole configuration, credentials and media trees (no
online role may read the first two whole) and writes only its own output
directory. It is therefore neither an online role nor an offline
maintenance profile: it runs beside the
online services, holding the startup interlock shared like them so it cannot
overlap offline work, but with its own closed mount inventory. The v1 launch
scope accepts this reduced escrow, a read-only view sealed to a human-held
key, in place of the deferred operator escrow workflow.
"""

import os
from pathlib import Path

from parishkit.config import ConfigError

from .backup import ARCHIVED_TREES
from .deployment import ServiceRole
from .runtime_paths import RuntimeLayout
from .service_boundaries import _kernel_pseudo_mount, kernel_mounts


def backup_targets(configuration):
    """Return destination/read-only pairs; inputs inside a read-only tree fold in."""
    layout = RuntimeLayout(configuration).validate()
    if configuration.service_role is not ServiceRole.BACKUP_WORKER:
        raise ConfigError("The backup profile is required.")
    if (
        configuration.configuration_file is None
        or configuration.postgres.password_file is None
    ):
        raise ConfigError("Backup configuration and database file mounts are required.")
    if (
        configuration.postgres.download_password_file is not None
        or configuration.valkey.password_file is not None
    ):
        raise ConfigError("The backup profile cannot reference online broker secrets.")
    if set(configuration.secrets) != {"backup_data"}:
        raise ConfigError("The backup profile receives only its recipient key.")
    archived = [configuration.paths[name] for name in ARCHIVED_TREES]
    trees = {tree: True for tree in archived} | {configuration.paths["backups"]: False}
    if configuration.paths["backups"] in archived or any(
        tree in configuration.paths["backups"].parents
        or configuration.paths["backups"] in tree.parents
        for tree in archived
    ):
        raise ConfigError("Backup output cannot live inside what it backs up.")
    targets = dict(trees)
    for path in (
        configuration.configuration_file,
        configuration.postgres.password_file,
        configuration.secrets["backup_data"],
        layout.interlock,
        # A replacement host cannot retarget without the completed record.
        layout.provisioning_record,
    ):
        # A file already under a read-only tree needs no mount of its own; one
        # outside every tree is mounted read-only by itself.
        if not any(tree in path.parents for tree, ro in trees.items() if ro):
            if path in targets:
                raise ConfigError("Backup inputs cannot alias one file.")
            targets[path] = True
    if any(
        configuration.paths["backups"] in path.parents
        for path in targets
        if path != configuration.paths["backups"]
    ):
        raise ConfigError("Backup inputs overlap the writable output.")
    return targets


def validate_backup_mounts(configuration, mounts):
    """A backup role declaration alone cannot authorize reading credentials."""
    expected = backup_targets(configuration)
    actual = {mount.target: mount for mount in mounts}
    if len(actual) != len(mounts):
        raise ConfigError("Overmounted backup paths are not admitted.")
    if Path("/") not in actual or not actual[Path("/")].read_only:
        raise ConfigError("Backup application root filesystem must be read-only.")
    if any(
        path not in actual or actual[path].read_only != ro
        for path, ro in expected.items()
    ):
        raise ConfigError("Backup mount inventory is incomplete or writable.")
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
        raise ConfigError("Backup service has an unrelated mount.")
    return configuration.service_role


def admit_backup_service(configuration):
    """The backup command uses actual process UID and Linux mount evidence."""
    if os.geteuid() == 0:
        raise ConfigError("The backup service cannot run as root.")
    return validate_backup_mounts(configuration, kernel_mounts())
