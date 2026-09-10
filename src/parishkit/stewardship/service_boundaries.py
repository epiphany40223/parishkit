"""Kernel mount evidence, not role strings, admits isolated online services."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from parishkit.config import ConfigError

from .deployment import DeploymentConfiguration, DeploymentProfile, ServiceRole

ALLOWED_SECRETS = {
    ServiceRole.WEB: frozenset(
        {
            "django_signing",
            "general_encryption",
            "family_code_mac",
            "token_public",
            "google_oauth",
            "metrics",
        }
    ),
    ServiceRole.WORKER: frozenset(
        {"general_encryption", "family_code_mac", "token_public", "parishsoft", "slack"}
    ),
    ServiceRole.SCHEDULER: frozenset({"token_public"}),
    ServiceRole.CONFIG_INSTALLER: frozenset(),
    ServiceRole.BACKUP_WORKER: frozenset({"backup_target", "backup_data"}),
    ServiceRole.MAIL_DISPATCH: frozenset(
        {"token_private", "token_public", "google_workspace"}
    ),
    ServiceRole.TOKEN_KEY_ROTATION: frozenset({"token_private", "token_public"}),
    ServiceRole.MIGRATION: frozenset(),
}


@dataclass(frozen=True)
class Mount:
    """Expose mount destination/access, never host source paths in diagnostics."""

    target: Path
    read_only: bool
    filesystem: str = ""


def parse_mounts(value):
    """Parse bounded Linux mountinfo and its documented destination escapes."""
    if type(value) is not str or len(value) > 1024 * 1024:
        raise ConfigError("Service mount inventory is unavailable.")
    result = []
    for line in value.splitlines():
        columns = line.split()
        if len(columns) < 10 or "-" not in columns:
            raise ConfigError("Service mount inventory is invalid.")
        target = re.sub(
            r"\\(040|011|012|134)", lambda match: chr(int(match[1], 8)), columns[4]
        )
        flags = set(columns[5].split(","))
        separator = columns.index("-")
        if (
            not target.startswith("/")
            or ("ro" in flags) == ("rw" in flags)
            or separator + 3 >= len(columns)
        ):
            raise ConfigError("Service mount inventory is invalid.")
        result.append(Mount(Path(target), "ro" in flags, columns[separator + 1]))
    if not result or len(result) > 4096:
        raise ConfigError("Service mount inventory is unavailable.")
    return tuple(result)


def kernel_mounts():
    """Linux-only production isolation includes Docker on macOS/Windows hosts."""
    try:
        with Path("/proc/self/mountinfo").open() as stream:
            return parse_mounts(stream.read(1024 * 1024 + 1))
    except OSError:
        raise ConfigError("Kernel service-mount evidence is unavailable.") from None


def validate_mounts(configuration, mounts):
    """Pure policy checker; operational callers must use kernel evidence below."""
    if not isinstance(configuration, DeploymentConfiguration):
        raise ConfigError("A validated deployment configuration is required.")
    role = configuration.service_role
    installer = role is ServiceRole.CREDENTIAL_INSTALLER
    if role not in ALLOWED_SECRETS and not installer:
        raise ConfigError("This profile needs its separate offline mount policy.")
    allowed = (
        {configuration.credential_target, "handoff_private"}
        if installer
        else ALLOWED_SECRETS[role]
    )
    if configuration.secrets.keys() - allowed:
        raise ConfigError("Service configuration contains forbidden secret paths.")
    required = {
        ServiceRole.MAIL_DISPATCH: {"token_private", "google_workspace"},
        ServiceRole.TOKEN_KEY_ROTATION: {"token_private", "token_public"},
        ServiceRole.BACKUP_WORKER: {"backup_target", "backup_data"},
    }.get(
        role,
        {"handoff_private", configuration.credential_target} if installer else set(),
    )
    if required - configuration.secrets.keys():
        raise ConfigError("Service credential mounts are incomplete.")
    mount_map = {mount.target: mount for mount in mounts}
    if len(mount_map) != len(mounts):
        raise ConfigError("Overmounted service paths are not admitted.")
    credential_root = configuration.paths["credentials"]
    authority = configuration.paths["config"] / "stewardship"
    target_directory = credential_root / str(configuration.credential_target)
    if (
        installer
        and configuration.secrets[configuration.credential_target].parent
        != target_directory
    ):
        raise ConfigError(
            "Installer credential file must be inside its writable target."
        )
    files = set(configuration.secrets.values())
    connection_paths = [
        path
        for path in (
            configuration.postgres.password_file,
            configuration.valkey.password_file,
        )
        if path is not None
    ]
    files.update(connection_paths)
    if len(files) != len(configuration.secrets) + len(connection_paths):
        raise ConfigError("Independent service credentials cannot alias one file.")
    for path in files:
        if (
            installer
            and path.parent == target_directory
            and path == configuration.secrets.get(configuration.credential_target)
        ):
            continue
        if path not in mount_map or not mount_map[path].read_only:
            raise ConfigError(
                "Consumers require individual read-only credential mounts."
            )
    if installer and (
        target_directory not in mount_map or mount_map[target_directory].read_only
    ):
        raise ConfigError("Installer requires exactly its target directory writable.")
    if authority not in mount_map or mount_map[authority].read_only != (
        role is not ServiceRole.CONFIG_INSTALLER
    ):
        raise ConfigError("Configuration authority mount has incorrect access.")
    for mount in mounts:
        _check_extra(
            configuration, mount, files, authority, target_directory, installer
        )
    return role


def _check_extra(configuration, mount, files, authority, target_directory, installer):
    """Unexpected mounts outside the configured credential root are forbidden too."""
    path, role = mount.target, configuration.service_role
    credential_root = configuration.paths["credentials"]
    if path == Path("/"):
        if not mount.read_only:
            raise ConfigError("Online application root filesystem must be read-only.")
        return
    if path in files or path == authority or (installer and path == target_directory):
        return
    if (
        path == credential_root
        or path in credential_root.parents
        or credential_root in path.parents
        or path in authority.parents
        or authority in path.parents
        or path.name in {"docker.sock", "containerd.sock"}
    ):
        raise ConfigError("Service has a broad or unrelated privileged mount.")
    if path in {Path("/etc/hosts"), Path("/etc/hostname"), Path("/etc/resolv.conf")}:
        return
    if mount.filesystem in {
        "proc",
        "sysfs",
        "tmpfs",
        "devpts",
        "mqueue",
        "cgroup2",
    } and (path == Path("/tmp") or path.parts[1] in {"proc", "sys", "dev"}):
        return
    if (
        configuration.profile is DeploymentProfile.DEVELOPMENT
        and path == Path("/app/src")
        and mount.read_only
    ):
        return
    if path == configuration.paths["config"] / "deployment.yaml" and mount.read_only:
        return
    data_paths = {
        ServiceRole.WEB: {"media", "reports"},
        ServiceRole.WORKER: {"media", "reports", "cache", "logs"},
        ServiceRole.BACKUP_WORKER: {"media"},
    }.get(role, set())
    if path in {configuration.paths[name] for name in data_paths}:
        if role is ServiceRole.BACKUP_WORKER and not mount.read_only:
            raise ConfigError("Backup service data mounts must be read-only.")
        return
    raise ConfigError("Service contains an unrecognized mount.")


def admit_online_service(configuration):
    """Entry-point admission cannot substitute supplied data for kernel evidence."""
    if os.geteuid() == 0:
        raise ConfigError("Online application services cannot run as root.")
    return validate_mounts(configuration, kernel_mounts())
