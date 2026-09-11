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
    if target:
        secrets[target] = config.paths["credentials"] / target / "credential"
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
    mounts += [Mount(path, True) for name, path in secrets.items() if name != target]
    if target:
        mounts.append(Mount(config.paths["credentials"] / target, False))
    return config, mounts


@pytest.mark.parametrize("role", list(ALLOWED_SECRETS))
def test_each_role_has_an_explicit_mount_allowlist(role):
    config, mounts = configured(role)
    assert validate_mounts(config, mounts) is role


@pytest.mark.parametrize(
    "target", ["/proc/self/mountinfo", "/dev/private", "/sys/private"]
)
def test_arbitrary_tmpfs_bind_is_not_a_kernel_pseudo_mount(target):
    """A pseudo-filesystem type does not authorize arbitrary privileged paths."""
    config, mounts = configured(ServiceRole.WEB)
    with pytest.raises(ConfigError, match="unrecognized"):
        validate_mounts(
            config, mounts + [Mount(Path(target), False, "tmpfs", "/", "tmpfs")]
        )


def test_pseudo_mount_source_and_root_are_checked_without_disclosure():
    """An unrelated bind cannot hide behind an otherwise admitted scratch path."""
    config, mounts = configured(ServiceRole.WEB)
    assert validate_mounts(
        config, mounts + [Mount(Path("/tmp"), False, "tmpfs", "/", "tmpfs")]
    )
    mount = Mount(Path("/tmp"), False, "tmpfs", "/private-root", "/private-source")
    assert "private" not in repr(mount)
    with pytest.raises(ConfigError, match="unrecognized") as error:
        validate_mounts(config, mounts + [mount])
    assert "private" not in str(error.value)


@pytest.mark.parametrize("path", ["/sbin/docker-init", "/usr/sbin/docker-init"])
def test_stock_init_executable_is_read_only_and_exact(path):
    """Compose init is allowed; an arbitrary file or writable init is not."""
    config, mounts = configured(ServiceRole.WEB)
    mount = Mount(
        Path(path), True, "overlay", "/usr/libexec/docker/docker-init", "overlay"
    )
    assert validate_mounts(config, [*mounts, mount]) is ServiceRole.WEB
    for invalid in (
        replace(mount, read_only=False),
        replace(mount, root="/private/docker-init"),
        replace(mount, root="/usr/libexec/docker"),
    ):
        with pytest.raises(ConfigError, match="unrecognized"):
            validate_mounts(config, [*mounts, invalid])


def test_authority_override_is_the_only_admitted_authority_mount():
    """An explicit runtime path cannot silently fall back to config/stewardship."""
    config, mounts = configured(ServiceRole.CONFIG_INSTALLER)
    previous = config.paths["authority"]
    config = replace(
        config,
        paths=replace(
            config.paths,
            values={
                **config.paths.values,
                "authority": Path("/custom/authority"),
            },
        ),
    )
    with pytest.raises(ConfigError, match="authority mount"):
        validate_mounts(config, mounts)
    mounts = [
        replace(mount, target=config.paths["authority"])
        if mount.target == previous
        else mount
        for mount in mounts
    ]
    assert validate_mounts(config, mounts) is ServiceRole.CONFIG_INSTALLER
    with pytest.raises(ConfigError):
        validate_mounts(config, mounts + [Mount(previous, False)])


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
