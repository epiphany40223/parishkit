"""Operational path overrides and owner-only admission never repair user data."""

import os
from dataclasses import replace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship.deployment import load_deployment
from parishkit.stewardship.runtime_paths import (
    RuntimeLayout,
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
