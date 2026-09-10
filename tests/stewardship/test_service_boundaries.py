"""Role declarations cannot compensate for forbidden kernel-visible mounts."""

from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import ServiceRole, load_deployment
from parishkit.stewardship.service_boundaries import (
    ALLOWED_SECRETS,
    Mount,
    parse_mounts,
    validate_mounts,
)


def configured(role, *, target=None):
    """Construct deployment metadata separately from actual mount-policy evidence."""
    config = load_deployment(environ={})
    names = ALLOWED_SECRETS[role] if target is None else {target, "handoff_private"}
    secrets = {name: Path("/run/secrets") / name for name in names}
    config = replace(
        config,
        service_role=role,
        credential_target=target,
        secrets=MappingProxyType(secrets),
    )
    mounts = [
        Mount(Path("/"), True),
        Mount(
            config.paths["config"] / "stewardship",
            role is not ServiceRole.CONFIG_INSTALLER,
        ),
    ]
    mounts += [Mount(path, True) for path in secrets.values()]
    if target:
        mounts.append(Mount(config.paths["credentials"] / target, False))
    return config, mounts


@pytest.mark.parametrize("role", list(ALLOWED_SECRETS))
def test_each_role_has_an_explicit_mount_allowlist(role):
    config, mounts = configured(role)
    assert validate_mounts(config, mounts) is role


@pytest.mark.parametrize(
    "role",
    [
        ServiceRole.WEB,
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
        ServiceRole.CONFIG_INSTALLER,
        ServiceRole.BACKUP_WORKER,
    ],
)
def test_general_services_cannot_name_or_hide_a_private_token_key(role):
    config, mounts = configured(role)
    with pytest.raises(ConfigError, match="forbidden secret"):
        validate_mounts(
            replace(
                config,
                secrets={**config.secrets, "token_private": Path("/hidden/private")},
            ),
            mounts,
        )
    with pytest.raises(ConfigError, match="unrecognized"):
        validate_mounts(config, mounts + [Mount(Path("/hidden/private"), True)])


def test_installer_target_isolated_and_handoff_key_is_read_only():
    config, mounts = configured(ServiceRole.CREDENTIAL_INSTALLER, target="google_oauth")
    assert validate_mounts(config, mounts) is ServiceRole.CREDENTIAL_INSTALLER
    with pytest.raises(ConfigError):
        validate_mounts(replace(config, credential_target="google_workspace"), mounts)
    with pytest.raises(ConfigError):
        validate_mounts(
            config, mounts + [Mount(config.paths["credentials"] / "slack", False)]
        )
    with pytest.raises(ConfigError):
        validate_mounts(
            config,
            [
                replace(mount, read_only=False)
                if mount.target == config.secrets["handoff_private"]
                else mount
                for mount in mounts
            ],
        )


def test_config_mount_is_writable_only_for_its_installer():
    config, mounts = configured(ServiceRole.WEB)
    with pytest.raises(ConfigError):
        validate_mounts(
            config,
            [
                replace(mount, read_only=False)
                if mount.target == config.paths["config"] / "stewardship"
                else mount
                for mount in mounts
            ],
        )
    for path in (
        config.paths.root,
        config.paths["credentials"],
        Path("/var/run/docker.sock"),
    ):
        with pytest.raises(ConfigError):
            validate_mounts(config, mounts + [Mount(path, True)])


def test_mountinfo_parser_uses_destination_flags_and_rejects_ambiguity():
    value = (
        "10 1 0:1 / / rw - overlay overlay rw\n"
        "11 10 8:1 /safe /key\\040file ro - ext4 /dev/disk rw\n"
    )
    assert parse_mounts(value)[1] == Mount(Path("/key file"), True, "ext4")
    for bad in (
        "",
        "garbage",
        value.replace(" ro ", " ro,rw "),
        "x" * (1024 * 1024 + 1),
    ):
        with pytest.raises(ConfigError):
            parse_mounts(bad)
