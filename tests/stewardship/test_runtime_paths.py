"""Operational path overrides and owner-only admission never repair user data."""

import os
from dataclasses import replace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import load_deployment
from parishkit.stewardship.runtime_paths import (
    RuntimeLayout,
    admit_credential_directory,
    explicit_path,
    private_directory,
)


def test_every_runtime_default_relocates_with_root(tmp_path):
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path)})
    layout = RuntimeLayout(configuration).validate()
    paths = [
        *configuration.paths.values.values(),
        layout.deployment_directory,
        layout.service_directory,
        layout.interlock,
        layout.credential("metrics"),
        layout.handoff("metrics"),
        layout.database_password("web"),
    ]
    assert all(path == tmp_path or tmp_path in path.parents for path in paths)


def test_individual_credential_override_is_preserved(tmp_path):
    configuration = load_deployment(environ={})
    path = tmp_path / "selected" / "metrics"
    layout = RuntimeLayout(replace(configuration, secrets={"metrics": path}))
    assert layout.credential("metrics") == path
    assert layout.credential_directory("metrics") == path.parent
    for target in ("../metrics", "handoff_private", "unknown"):
        with pytest.raises(ConfigError):
            layout.credential(target)
    with pytest.raises(ConfigError):
        layout.database_password("../../database")


@pytest.mark.parametrize(
    "name", [".bootstrap-candidate", ".replacement.json", ".replacement.lock"]
)
def test_credential_cannot_overwrite_its_own_journal_or_lock(tmp_path, name):
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path)})
    configuration = replace(
        configuration, secrets={"metrics": tmp_path / "isolated-target" / name}
    )
    with pytest.raises(ConfigError, match="aliases"):
        RuntimeLayout(configuration).validate()


@pytest.mark.parametrize("name", ["credentials", "authority", "media", "postgresql"])
def test_storage_cannot_alias_or_contain_exports(tmp_path, name):
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path)})
    configuration = replace(
        configuration,
        paths=replace(
            configuration.paths,
            values={**configuration.paths.values, name: configuration.paths["reports"]},
        ),
    )
    with pytest.raises(ConfigError, match="overlap"):
        RuntimeLayout(configuration).validate()


@pytest.mark.parametrize("value", ["relative", "/", "/tmp/../private"])
def test_nonconcrete_storage_paths_are_rejected(value):
    with pytest.raises(ConfigError):
        explicit_path(value)


def test_private_leaf_creation_and_existing_safety(tmp_path):
    target = tmp_path / "exports"
    assert private_directory(target, create=True) == target
    assert private_directory(target) == target
    with pytest.raises(ConfigError):
        private_directory(target, owner=os.geteuid() + 1)
    target.chmod(0o755)
    with pytest.raises(ConfigError):
        private_directory(target, create=True)
    assert target.stat().st_mode & 0o777 == 0o755
    with pytest.raises(ConfigError):
        private_directory(tmp_path / "absent")
    with pytest.raises(ConfigError):
        private_directory(tmp_path / "missing-parent" / "leaf", create=True)


def test_file_and_symlink_targets_are_preserved(tmp_path):
    file = tmp_path / "file"
    file.write_text("preserve")
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    for path in (file, link, link / "child"):
        with pytest.raises(ConfigError):
            private_directory(path, create=True)
    assert file.read_text() == "preserve"
    assert link.is_symlink()


@pytest.mark.parametrize("kind", ["download", "configuration", "ordinary", "valkey"])
def test_credential_target_cannot_replace_another_runtime_input(tmp_path, kind):
    """An individual read-only bind is insufficient if its parent is writable."""
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path)})
    protected = tmp_path / "credentials" / "metrics" / "other-input"
    if kind == "download":
        configuration = replace(
            configuration,
            postgres=replace(configuration.postgres, download_password_file=protected),
        )
    elif kind == "ordinary":
        configuration = replace(
            configuration,
            postgres=replace(configuration.postgres, password_file=protected),
        )
    elif kind == "valkey":
        configuration = replace(
            configuration, valkey=replace(configuration.valkey, password_file=protected)
        )
    else:
        configuration = replace(configuration, configuration_file=protected)
    with pytest.raises(ConfigError, match="unrelated runtime inputs"):
        RuntimeLayout(configuration).validate()


@pytest.mark.parametrize(
    "storage", ["reports", "cache", "media", "authority", "postgresql"]
)
def test_runtime_password_cannot_be_exposed_through_other_storage(tmp_path, storage):
    """Public/static or independently writable trees cannot contain SQL passwords."""
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path)})
    configuration = replace(
        configuration,
        postgres=replace(
            configuration.postgres,
            download_password_file=configuration.paths[storage] / "password",
        ),
    )
    with pytest.raises(ConfigError, match="protected storage"):
        RuntimeLayout(configuration).validate()


def test_database_and_broker_inputs_cannot_alias(tmp_path):
    """Independent service authentication needs independent input files."""
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path)})
    path = tmp_path / "private-password"
    configuration = replace(
        configuration,
        postgres=replace(configuration.postgres, password_file=path),
        valkey=replace(configuration.valkey, password_file=path),
    )
    with pytest.raises(ConfigError, match="alias"):
        RuntimeLayout(configuration).validate()


@pytest.mark.parametrize(
    "storage", ["reports", "media", "authority", "postgresql", "cache"]
)
def test_service_configuration_cannot_live_in_writable_storage(tmp_path, storage):
    """Read-only metadata mounts cannot be replaced through a broad data mount."""
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path)})
    with pytest.raises(ConfigError, match="protected storage"):
        RuntimeLayout(
            replace(
                configuration,
                configuration_file=configuration.paths[storage] / "web.yaml",
            )
        ).validate()


def test_valkey_acl_directory_is_reserved_even_with_password_override(tmp_path):
    """A credential installer never receives write authority over server ACLs."""
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(tmp_path)})
    configuration = replace(
        configuration,
        secrets={"metrics": tmp_path / "credentials" / "valkey" / "metrics"},
    )
    with pytest.raises(ConfigError, match="protected storage"):
        RuntimeLayout(configuration).validate()


@pytest.mark.parametrize(
    "kind", ["unrelated", "directory", "symlink", "hardlink", "public"]
)
def test_writable_credential_parent_must_be_dedicated(tmp_path, kind):
    """An external path override cannot grant write access to a shared directory."""
    target = tmp_path / "metrics.json"
    if kind == "directory":
        (tmp_path / "other").mkdir(mode=0o700)
    elif kind == "symlink":
        target.symlink_to(tmp_path / "absent")
    elif kind == "hardlink":
        target.write_text("value")
        os.link(target, tmp_path / ".replacement.json")
        target.chmod(0o600)
    else:
        selected = target if kind == "public" else tmp_path / "other"
        selected.write_text("preserve")
        selected.chmod(0o644 if kind == "public" else 0o600)
    with pytest.raises(ConfigError):
        admit_credential_directory(target)


def test_dedicated_credential_parent_accepts_only_private_protocol_files(tmp_path):
    """Interrupted writer temporaries do not widen the accepted namespace."""
    for name in (
        "metrics.json",
        ".replacement.lock",
        ".replacement.json",
        ".metrics.json.a1234567.tmp",
    ):
        path = tmp_path / name
        path.write_text("private")
        path.chmod(0o600)
    admit_credential_directory(tmp_path / "metrics.json")
