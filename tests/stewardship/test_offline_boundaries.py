"""Operator profiles have explicit target mounts, not broad repair authority."""

from dataclasses import replace
from pathlib import Path

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.offline_boundaries import (
    offline_targets,
    validate_offline_mounts,
)
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.service_boundaries import Mount

from .bootstrap_factory import bootstrap_fixture


def configuration(tmp_path, role=ServiceRole.BOOTSTRAP):
    """Exact inputs and independent generated targets model the operator profile."""
    config, _ = bootstrap_fixture(tmp_path)
    config = replace(
        config, configuration_file=tmp_path / "config" / "services" / "bootstrap.yaml"
    )
    if role is not ServiceRole.BOOTSTRAP:
        config = replace(config, service_role=role, secrets={})
    if role is ServiceRole.DATABASE_PROVISION:
        config = replace(
            config,
            postgres=replace(
                config.postgres,
                password_file=RuntimeLayout(config).database_password("operator"),
            ),
        )
    mounts = [Mount(Path("/"), True)] + [
        Mount(path, ro) for path, ro in offline_targets(config).items()
    ]
    return config, mounts


@pytest.mark.parametrize(
    "role",
    [
        ServiceRole.BOOTSTRAP,
        ServiceRole.MIGRATION,
        ServiceRole.ADMIN_RECOVERY,
        ServiceRole.DATABASE_PROVISION,
    ],
)
def test_offline_roles_have_distinct_authority(tmp_path, role):
    """Recovery is the config writer only; migration has no config/secret write path."""
    config, mounts = configuration(tmp_path, role)
    assert validate_offline_mounts(config, mounts) is role
    writable = {mount.target for mount in mounts if not mount.read_only}
    if role in {ServiceRole.MIGRATION, ServiceRole.DATABASE_PROVISION}:
        assert not writable
    elif role is ServiceRole.ADMIN_RECOVERY:
        assert writable == {config.paths["authority"]}
    else:
        assert RuntimeLayout(config).credential_directory("token_private") in writable
        assert (
            RuntimeLayout(config).credential_directory("google_workspace")
            not in writable
        )
    for index, mount in enumerate(mounts):
        with pytest.raises(ConfigError):
            validate_offline_mounts(config, mounts[:index] + mounts[index + 1 :])
        changed = mounts.copy()
        changed[index] = replace(mount, read_only=not mount.read_only)
        with pytest.raises(ConfigError):
            validate_offline_mounts(config, changed)


@pytest.mark.parametrize("target", ["credentials", "config", "run", "reports", "media"])
def test_offline_profile_rejects_broad_data_mounts(tmp_path, target):
    """Offline does not mean unrestricted host or campaign access."""
    config, mounts = configuration(tmp_path)
    with pytest.raises(ConfigError):
        validate_offline_mounts(config, mounts + [Mount(config.paths[target], False)])
    with pytest.raises(ConfigError):
        validate_offline_mounts(
            config, mounts + [Mount(Path("/var/run/docker.sock"), True)]
        )


def test_offline_container_infrastructure_and_development_source_are_admitted(tmp_path):
    """Normal Docker support mounts coexist with the exact offline authority set."""
    config, mounts = configuration(tmp_path)
    infrastructure = [
        Mount(Path(path), True)
        for path in (
            "/etc/hosts",
            "/etc/hostname",
            "/etc/resolv.conf",
            "/app/src",
        )
    ]
    infrastructure += [
        Mount(Path("/tmp"), False, "tmpfs", "/", "tmpfs"),
        Mount(Path("/proc"), False, "proc", "/", "proc"),
        Mount(
            Path("/sbin/docker-init"),
            True,
            "overlay",
            "/usr/libexec/docker/docker-init",
            "overlay",
        ),
    ]
    assert (
        validate_offline_mounts(config, [*mounts, *infrastructure])
        is ServiceRole.BOOTSTRAP
    )


def test_offline_alias_unknown_secret_and_online_role_refuse(tmp_path):
    """Declaration-only role swaps and writable-input aliases cannot pass admission."""
    config, mounts = configuration(tmp_path)
    for bad in (
        replace(config, service_role=ServiceRole.WEB),
        replace(config, configuration_file=None),
        replace(config, configuration_file=config.postgres.password_file),
        replace(config, configuration_file=config.secrets["google_oauth"]),
        replace(
            config, secrets={**config.secrets, "google_workspace": Path("/mail/key")}
        ),
        replace(
            config,
            configuration_file=RuntimeLayout(config).deployment_directory
            / "input.yaml",
        ),
    ):
        with pytest.raises(ConfigError):
            validate_offline_mounts(bad, mounts)


@pytest.mark.parametrize("kind", ["download", "valkey"])
def test_offline_profile_refuses_online_connection_secrets(tmp_path, kind):
    """Unused online credential references must not widen an offline profile."""
    config, _ = configuration(tmp_path)
    path = tmp_path / "extra-password"
    if kind == "download":
        config = replace(
            config, postgres=replace(config.postgres, download_password_file=path)
        )
    else:
        config = replace(config, valkey=replace(config.valkey, password_file=path))
    with pytest.raises(ConfigError, match="online connection secrets"):
        offline_targets(config)
