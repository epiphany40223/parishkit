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
    mounts = [Mount(Path("/"), True)] + [
        Mount(path, ro) for path, ro in offline_targets(config).items()
    ]
    return config, mounts


@pytest.mark.parametrize(
    "role", [ServiceRole.BOOTSTRAP, ServiceRole.MIGRATION, ServiceRole.ADMIN_RECOVERY]
)
def test_offline_roles_have_distinct_authority(tmp_path, role):
    """Recovery is the config writer only; migration has no config/secret write path."""
    config, mounts = configuration(tmp_path, role)
    assert validate_offline_mounts(config, mounts) is role
    writable = {mount.target for mount in mounts if not mount.read_only}
    if role is ServiceRole.MIGRATION:
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
