"""Resolved operational storage targets and strict, non-destructive admission.

The host/operator provisions ownership. Online containers never chown, chmod or
repair an existing path, and generic temporary cleanup has no authority here.
All application containers use one filesystem UID inside separate namespaces:
owner-only credential files can then be shared by exact read-only file mounts
without broadening modes. SQL identities and mount authority remain distinct.
"""

import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from parishkit.config import ConfigError

from .deployment import DeploymentConfiguration

APPLICATION_UID = 10001
APPLICATION_GID = 10001


def explicit_path(value):
    """Accept a concrete non-root absolute path without any symlink traversal."""
    path = Path(value)
    if (
        not path.is_absolute()
        or path == Path(path.anchor)
        or ".." in path.parts
        or any(parent.is_symlink() for parent in (path, *path.parents))
    ):
        raise ConfigError("Runtime storage requires an explicit non-symlink path.")
    return path


def private_directory(value, *, create=False, owner=None):
    """Create one requested leaf or validate it; never repair/adopt unsafe state."""
    path = explicit_path(value)
    expected = os.geteuid() if owner is None else owner
    try:
        if create:
            with suppress(FileExistsError):
                path.mkdir(mode=0o700)
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != expected
        ):
            raise ConfigError("Runtime directory ownership or mode is invalid.")
    except OSError:
        raise ConfigError("Runtime directory is unavailable.") from None
    return path


@dataclass(frozen=True)
class RuntimeLayout:
    """Additional narrow targets derive from the already overridden shared paths."""

    configuration: DeploymentConfiguration

    @property
    def deployment_directory(self):
        """A narrow initial-write target, never the entire configuration tree."""
        return self.configuration.paths["config"] / "deployment"

    @property
    def service_directory(self):
        """Operator-rendered per-process metadata contains references, not secrets."""
        return self.configuration.paths["config"] / "services"

    @property
    def interlock(self):
        """One stable inode, not a replaceable broad run-directory write mount."""
        return self.configuration.paths["run"] / "startup.lock"

    def credential_directory(self, target):
        """Select only an enumerated target; the caller cannot nominate a path."""
        from .deployment import SECRET_NAMES

        if target not in SECRET_NAMES - {"handoff_private"}:
            raise ConfigError("Unknown credential target.")
        selected = self.configuration.secrets.get(target)
        return (
            selected.parent
            if selected is not None
            else self.configuration.paths["credentials"] / target
        )

    def credential(self, target):
        """Honor an explicit individual credential override before its default."""
        directory = self.credential_directory(target)
        path = self.configuration.secrets.get(target, directory / "credential")
        if path.name in {
            ".bootstrap-candidate",
            ".replacement.json",
            ".replacement.lock",
        }:
            raise ConfigError("Credential file aliases an internal journal or lock.")
        return path

    def handoff(self, target):
        """Each installer receives one independent handoff private-key file."""
        self.credential_directory(target)  # Closed target validation.
        return (
            self.configuration.paths["credentials"] / "handoff" / target / "credential"
        )

    def database_password(self, identity):
        """Keep separately authenticated SQL passwords on individual file mounts."""
        from .deployment import SECRET_NAMES, ServiceRole

        roles = {role.value for role in ServiceRole} | {"operator", "download"}
        roles |= {
            "credential-installer-" + target.replace("_", "-")
            for target in SECRET_NAMES - {"handoff_private"}
        }
        if identity not in roles:
            raise ConfigError("Unknown runtime database identity.")
        return self.configuration.paths["credentials"] / "database" / identity

    def validate(self):
        """Reject aliases/overlaps that would expose secrets through data mounts."""
        paths = self.configuration.paths
        roots = [
            explicit_path(paths[name])
            for name in (
                "authority",
                "credentials",
                "reports",
                "media",
                "cache",
                "logs",
            )
        ]
        roots += [explicit_path(self.deployment_directory)]
        for index, path in enumerate(roots):
            for other in roots[index + 1 :]:
                if path == other or path in other.parents or other in path.parents:
                    raise ConfigError("Independent runtime storage targets overlap.")
        stores = [
            explicit_path(paths[name]) for name in ("postgresql", "valkey", "caddy")
        ]
        for index, path in enumerate(stores):
            for other in [*roots, *stores[index + 1 :]]:
                if path == other or path in other.parents or other in path.parents:
                    raise ConfigError("Persistent runtime stores overlap.")
        explicit_path(self.interlock)
        self._validate_credential_targets(roots, stores)
        return self

    def _validate_credential_targets(self, roots, stores):
        """Overridden writable targets cannot contain another secret or data store."""
        from .deployment import SECRET_NAMES

        credential_root = self.configuration.paths["credentials"]
        for name in SECRET_NAMES - {"handoff_private"}:
            explicit_path(self.credential(name))
        targets = [
            explicit_path(self.credential_directory(name))
            for name in sorted(SECRET_NAMES - {"handoff_private"})
        ]
        reserved = [root for root in roots if root != credential_root] + stores
        reserved += [credential_root / "handoff", credential_root / "database"]
        for index, target in enumerate(targets):
            if target == credential_root or target in credential_root.parents:
                raise ConfigError("Credential target is too broad.")
            for other in [*targets[index + 1 :], *reserved]:
                if (
                    target == other
                    or target in other.parents
                    or other in target.parents
                ):
                    raise ConfigError("Credential targets overlap protected storage.")
        for path in (
            self.interlock,
            self.configuration.postgres.password_file,
            self.configuration.valkey.password_file,
        ):
            if path is not None and any(
                path == target or target in path.parents for target in targets
            ):
                raise ConfigError(
                    "Credential targets contain unrelated runtime inputs."
                )
