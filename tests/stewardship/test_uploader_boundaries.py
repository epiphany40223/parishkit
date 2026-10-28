"""The off-site uploader mounts exactly its small inventory, read-only (#235)."""

from dataclasses import replace
from pathlib import Path

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import uploader_boundaries as boundaries
from parishkit.stewardship.deployment import DeploymentProfile, ServiceRole
from parishkit.stewardship.local.clock import CLOCK_MOUNT_TARGET
from parishkit.stewardship.runtime_grants import (
    BACKUP_DESTINATION_FUNCTION,
    DEBUG_OFF_FUNCTION,
    login_name,
    runtime_functions,
    runtime_grants,
)
from parishkit.stewardship.runtime_network import (
    DEFAULT_OFFLINE,
    OFFLINE_PROFILES,
    RuntimeNetwork,
)
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.runtime_topology import _service_config
from parishkit.stewardship.service_boundaries import (
    ALLOWED_SECRETS,
    Mount,
    validate_mounts,
)

from .test_runtime_topology import configuration_at


def uploader_configuration(tmp_path):
    """The rendered uploader profile's own configuration."""
    return _service_config(configuration_at(tmp_path), ServiceRole.BACKUP_UPLOADER)


def mounts(targets, *extra):
    """Kernel-style mounts for the expected targets plus the container root."""
    result = [Mount(Path("/"), True)]
    result += [Mount(path, ro) for path, ro in targets.items()]
    return result + list(extra)


def test_targets_are_the_document_password_lock_key_directory_and_backups(tmp_path):
    """Five read-only paths: nothing writable, no tree, no recipient key."""
    configuration = uploader_configuration(tmp_path)
    layout = RuntimeLayout(configuration)
    targets = boundaries.uploader_targets(configuration)
    assert targets == {
        configuration.configuration_file: True,
        layout.database_password("backup-uploader"): True,
        layout.interlock: True,
        layout.credential("google_workspace").parent: True,
        configuration.paths["backups"]: True,
    }
    for tree in ("config", "credentials", "media", "authority"):
        assert configuration.paths[tree] not in targets
    assert layout.provisioning_record not in targets
    assert set(configuration.secrets) == {"google_workspace"}
    assert configuration.postgres.user == login_name(ServiceRole.BACKUP_UPLOADER)
    assert configuration.valkey.password_file is None
    assert configuration.postgres.download_password_file is None
    assert boundaries.validate_uploader_mounts(configuration, mounts(targets)) is (
        ServiceRole.BACKUP_UPLOADER
    )


def test_an_overridden_key_path_is_mounted_by_itself(tmp_path):
    """As mail-dispatch does: no rotating directory, the single file instead."""
    configuration = uploader_configuration(tmp_path)
    key = tmp_path / "elsewhere" / "workspace.json"
    moved = replace(configuration, secrets={"google_workspace": key})
    targets = boundaries.uploader_targets(moved)
    assert targets[key] is True
    assert RuntimeLayout(moved).credential("google_workspace").parent not in targets


def test_other_roles_secrets_and_online_references_are_refused(tmp_path):
    """Only the uploader role, only the Workspace key, no broker or download."""
    configuration = uploader_configuration(tmp_path)
    with pytest.raises(ConfigError, match="uploader profile is required"):
        boundaries.uploader_targets(
            replace(configuration, service_role=ServiceRole.BACKUP_WORKER)
        )
    with pytest.raises(ConfigError, match="only the Workspace key"):
        boundaries.uploader_targets(
            replace(
                configuration,
                secrets={
                    **configuration.secrets,
                    "backup_data": RuntimeLayout(configuration).credential(
                        "backup_data"
                    ),
                },
            )
        )
    with pytest.raises(ConfigError, match="online secrets"):
        boundaries.uploader_targets(
            replace(
                configuration,
                valkey=replace(configuration.valkey, password_file=tmp_path / "v"),
            )
        )
    # A key path overridden into the backups directory would be mounted
    # twice; the layout refuses most overlaps, and the inventory the rest.
    nested = replace(
        configuration,
        secrets={"google_workspace": configuration.paths["backups"] / "key.json"},
    )
    with pytest.raises(ConfigError, match="overlap|Independent runtime storage"):
        boundaries.uploader_targets(nested)


@pytest.mark.parametrize(
    "change",
    ["writable backups", "writable root", "missing key", "extra tree", "duplicate"],
)
def test_mount_evidence_must_be_exactly_the_inventory(tmp_path, change):
    """A writable, missing, extra or overmounted path refuses startup."""
    configuration = uploader_configuration(tmp_path)
    targets = boundaries.uploader_targets(configuration)
    evidence = mounts(targets)
    key = RuntimeLayout(configuration).credential("google_workspace").parent
    if change == "writable backups":
        evidence = [
            Mount(m.target, False) if m.target == configuration.paths["backups"] else m
            for m in evidence
        ]
    elif change == "writable root":
        evidence = [Mount(Path("/"), False), *evidence[1:]]
    elif change == "missing key":
        evidence = [m for m in evidence if m.target != key]
    elif change == "extra tree":
        evidence.append(Mount(configuration.paths["credentials"], True))
    else:
        evidence.append(Mount(configuration.paths["backups"], True))
    with pytest.raises(ConfigError):
        boundaries.validate_uploader_mounts(configuration, evidence)


def test_local_clock_and_development_source_are_the_only_extra_mounts(tmp_path):
    """The same two exceptions the backup profile has, read-only only."""
    configuration = uploader_configuration(tmp_path)
    targets = boundaries.uploader_targets(configuration)
    with pytest.raises(ConfigError, match="unrelated mount"):
        boundaries.validate_uploader_mounts(
            configuration, mounts(targets, Mount(CLOCK_MOUNT_TARGET, True))
        )
    local = replace(configuration, profile=DeploymentProfile.LOCAL)
    assert boundaries.validate_uploader_mounts(
        local, mounts(targets, Mount(CLOCK_MOUNT_TARGET, True))
    )
    with pytest.raises(ConfigError, match="unrelated mount"):
        boundaries.validate_uploader_mounts(
            local, mounts(targets, Mount(CLOCK_MOUNT_TARGET, False))
        )


def test_the_uploader_is_not_an_online_role(tmp_path):
    """The online mount policy refuses it: it has only its own policy."""
    configuration = uploader_configuration(tmp_path)
    assert ServiceRole.BACKUP_UPLOADER not in ALLOWED_SECRETS
    with pytest.raises(ConfigError, match="separate offline mount policy"):
        validate_mounts(
            configuration, mounts(boundaries.uploader_targets(configuration))
        )


def test_the_uploaders_grants_registry_is_exactly_the_design():
    """Its outcome rows, three run columns, the runtime row's id, timeout logs."""
    tables, columns = runtime_grants(ServiceRole.BACKUP_UPLOADER)
    assert tables == {
        "stewardship_backup_upload": {"SELECT", "INSERT"},
        "stewardship_operational_log": {"INSERT"},
    }
    assert columns == {
        "stewardship_backup_run": {
            "SELECT": {"manifest_digest", "started_at", "completed_at"}
        },
        "stewardship_system_configuration": {"SELECT": {"id"}},
    }
    assert runtime_functions(ServiceRole.BACKUP_UPLOADER) == {
        BACKUP_DESTINATION_FUNCTION,
        DEBUG_OFF_FUNCTION,
    }
    assert login_name(ServiceRole.BACKUP_UPLOADER) == "pk_stewardship_backup_uploader"


def test_the_uploaders_slice_is_index_five_of_offline():
    """The subnet-overlap preflight computes the /28 at index 5; keep them tied.

    ``tools/stewardship-upgrade-host.sh`` cannot import this module, so it
    hard-codes the index; this test fails if the profile ever moves.
    """
    assert OFFLINE_PROFILES.index("backup-uploader") == 5
    network = RuntimeNetwork()
    assert network.offline == DEFAULT_OFFLINE
    assert str(network.offline_subnet("backup-uploader")) == "172.29.242.80/28"
    assert network.offline_source("backup-uploader") == "172.29.242.88/29"
    assert network.offline_postgres("backup-uploader") == "172.29.242.82"
    small = RuntimeNetwork(offline="10.20.30.0/25")
    assert str(small.offline_subnet("backup-uploader")) == "10.20.30.80/28"
