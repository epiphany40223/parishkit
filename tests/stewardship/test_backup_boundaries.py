"""The backup profile mounts exactly its trees, key and output, nothing else."""

from dataclasses import replace
from pathlib import Path

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import backup_boundaries as boundaries
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.runtime_topology import _service_config
from parishkit.stewardship.service_boundaries import Mount

from .test_runtime_topology import configuration_at


def backup_configuration(tmp_path):
    """The rendered backup profile's own configuration."""
    return _service_config(configuration_at(tmp_path), ServiceRole.BACKUP_WORKER)


def mounts(targets, *extra):
    """Kernel-style mounts for the expected targets plus the container root."""
    result = [Mount(Path("/"), True)]
    result += [Mount(path, ro) for path, ro in targets.items()]
    return result + list(extra)


def test_targets_are_the_three_trees_the_key_the_lock_and_the_output(tmp_path):
    """Inputs inside the read-only trees fold in; the output is the one write."""
    configuration = backup_configuration(tmp_path)
    layout = RuntimeLayout(configuration)
    targets = boundaries.backup_targets(configuration)
    assert targets == {
        configuration.paths["config"]: True,
        configuration.paths["credentials"]: True,
        configuration.paths["media"]: True,
        configuration.paths["backups"]: False,
        layout.interlock: True,
        layout.provisioning_record: True,
    }
    assert configuration.paths["backups"] == tmp_path / "backups"
    assert set(configuration.secrets) == {"backup_data"}
    assert boundaries.validate_backup_mounts(configuration, mounts(targets)) is (
        ServiceRole.BACKUP_WORKER
    )


def test_other_roles_secrets_and_overlapping_output_are_refused(tmp_path):
    """Only the backup role, only its key, and never an output inside a tree."""
    configuration = backup_configuration(tmp_path)
    with pytest.raises(ConfigError, match="backup profile is required"):
        boundaries.backup_targets(replace(configuration, service_role=ServiceRole.WEB))
    elsewhere = tmp_path.parent / (tmp_path.name + "-ps") / "parishsoft" / "credential"
    with pytest.raises(ConfigError, match="only its recipient key"):
        boundaries.backup_targets(
            replace(
                configuration,
                secrets={**configuration.secrets, "parishsoft": elsewhere},
            )
        )
    inside = replace(
        configuration,
        paths=replace(
            configuration.paths,
            values={
                **configuration.paths.values,
                "backups": configuration.paths["config"] / "backups",
            },
        ),
    )
    with pytest.raises(ConfigError, match="inside what it backs up"):
        boundaries.backup_targets(inside)


def test_an_authority_override_still_renders_a_backup_profile(tmp_path):
    """The override stays supported: only running a backup refuses it."""
    configuration = backup_configuration(tmp_path)
    moved = replace(
        configuration,
        paths=replace(
            configuration.paths,
            values={
                **configuration.paths.values,
                "authority": tmp_path.parent / (tmp_path.name + "-authority"),
            },
        ),
    )
    assert configuration.paths["config"] in boundaries.backup_targets(moved)


def test_mount_evidence_must_match_exactly(tmp_path):
    """A missing tree, a writable tree, an extra mount or a writable root refuse."""
    configuration = backup_configuration(tmp_path)
    targets = boundaries.backup_targets(configuration)
    short = {k: v for k, v in targets.items() if k != configuration.paths["config"]}
    with pytest.raises(ConfigError, match="incomplete or writable"):
        boundaries.validate_backup_mounts(configuration, mounts(short))
    writable = {**targets, configuration.paths["credentials"]: False}
    with pytest.raises(ConfigError, match="incomplete or writable"):
        boundaries.validate_backup_mounts(configuration, mounts(writable))
    with pytest.raises(ConfigError, match="unrelated mount"):
        boundaries.validate_backup_mounts(
            configuration, mounts(targets, Mount(tmp_path / "run" / "persistent", True))
        )
    root_writable = [Mount(Path("/"), False)] + mounts(targets)[1:]
    with pytest.raises(ConfigError, match="root filesystem"):
        boundaries.validate_backup_mounts(configuration, root_writable)
