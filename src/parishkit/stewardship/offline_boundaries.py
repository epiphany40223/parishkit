"""Separate narrow kernel-mount policies for explicit offline operator profiles.

These profiles are not online roles and never inherit an online allowance for
whole credentials, provider secrets, campaign data or runtime storage trees.
The process must additionally hold its real exclusive StartupLease before any
write or database mutation. Host-root authority is outside this threat model.
"""

import os
from pathlib import Path

from parishkit.config import ConfigError

from .deployment import ServiceRole
from .runtime_paths import RuntimeLayout
from .service_boundaries import _kernel_pseudo_mount, kernel_mounts


def offline_targets(configuration):
    """Return exact destination/access pairs, with no root-tree write exception."""
    layout = RuntimeLayout(configuration).validate()
    role = configuration.service_role
    if role not in {
        ServiceRole.BOOTSTRAP,
        ServiceRole.MIGRATION,
        ServiceRole.ADMIN_RECOVERY,
        ServiceRole.DATABASE_PROVISION,
    }:
        raise ConfigError("An explicit offline service profile is required.")
    if (
        configuration.configuration_file is None
        or configuration.postgres.password_file is None
    ):
        raise ConfigError(
            "Offline configuration and database file mounts are required."
        )
    targets = {
        configuration.configuration_file: True,
        configuration.postgres.password_file: True,
        layout.interlock: True,
    }
    if role is ServiceRole.BOOTSTRAP:
        from .bootstrap import HANDOFF_TARGETS, INITIAL_TARGETS

        if configuration.secrets.keys() - {*INITIAL_TARGETS, "google_oauth"}:
            raise ConfigError("Bootstrap has an unrelated credential reference.")
        oauth = configuration.secrets.get("google_oauth")
        if oauth is None:
            raise ConfigError("Bootstrap requires its operator OAuth file.")
        if oauth in targets:
            raise ConfigError("Offline inputs cannot alias one file.")
        targets[oauth] = True
        writable = {
            layout.deployment_directory: False,
            configuration.paths["authority"]: False,
            **{layout.credential_directory(name): False for name in INITIAL_TARGETS},
            **{layout.handoff(name).parent: False for name in HANDOFF_TARGETS},
        }
        if targets.keys() & writable.keys():
            raise ConfigError("Offline inputs overlap writable targets.")
        targets.update(writable)
    else:
        if configuration.secrets:
            raise ConfigError("Offline maintenance must not receive provider secrets.")
        if role is ServiceRole.ADMIN_RECOVERY:
            targets[configuration.paths["authority"]] = False
        if role is ServiceRole.DATABASE_PROVISION:
            from .runtime_identities import database_identities

            for name, _, _, _ in database_identities():
                path = layout.database_password(name)
                if path in targets:
                    raise ConfigError("Database provisioning inputs alias one file.")
                targets[path] = True
    if len(targets) < 3:
        raise ConfigError("Offline inputs cannot alias one file.")
    # No individual readonly input may hide inside a writable provisioning mount.
    for path, read_only in targets.items():
        if read_only and any(
            not other_read_only and (path == other or other in path.parents)
            for other, other_read_only in targets.items()
        ):
            raise ConfigError("Offline inputs overlap writable targets.")
    return targets


def validate_offline_mounts(configuration, mounts):
    """An offline role declaration alone cannot authorize provisioning authority."""
    expected = offline_targets(configuration)
    actual = {mount.target: mount for mount in mounts}
    if len(actual) != len(mounts):
        raise ConfigError("Overmounted offline paths are not admitted.")
    if Path("/") not in actual or not actual[Path("/")].read_only:
        raise ConfigError("Offline application root filesystem must be read-only.")
    if any(
        path not in actual or actual[path].read_only != ro
        for path, ro in expected.items()
    ):
        raise ConfigError("Offline mount inventory is incomplete or writable.")
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
        raise ConfigError("Offline service has an unrelated mount.")
    return configuration.service_role


def admit_offline_service(configuration):
    """The operational command uses actual process UID and Linux mount evidence."""
    if os.geteuid() == 0:
        raise ConfigError("Offline application services cannot run as root.")
    return validate_offline_mounts(configuration, kernel_mounts())
