"""Pre-migration provisioning has no database/provider dependency or overwrite path."""

import json
from dataclasses import replace
from uuid import uuid4

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import bootstrap
from parishkit.stewardship.accounts.authority import AuthorityStore
from parishkit.stewardship.accounts.configuration_schema import validate_sections
from parishkit.stewardship.accounts.key_files import (
    load_keyring,
    read_private,
    write_private,
)
from parishkit.stewardship.bootstrap import (
    HANDOFF_TARGETS,
    INITIAL_TARGETS,
    BootstrapIdentity,
    provision_initial_files,
)
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.startup_interlock import StartupBusy, StartupLease

from .bootstrap_factory import bootstrap_fixture


def inventory(configuration):
    """Snapshot disposable fixture files for exact preservation assertions."""
    return {
        path: path.read_bytes()
        for path in configuration.paths.root.rglob("*")
        if path.is_file()
    }


def test_pre_migration_is_private_complete_and_idempotent(tmp_path):
    """All purposes get independent keys while the sealed-box public pair matches."""
    configuration, identity = bootstrap_fixture(tmp_path)
    layout = RuntimeLayout(configuration)
    root = provision_initial_files(configuration, identity)
    assert (
        AuthorityStore(configuration.paths["authority"], validate_sections).active()
        == root
    )
    previous = inventory(configuration)
    assert provision_initial_files(configuration, identity) == root
    assert inventory(configuration) == previous
    paths = [
        *(layout.credential(target) for target in INITIAL_TARGETS),
        *(layout.handoff(target) for target in HANDOFF_TARGETS),
    ]
    for path in paths:
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    keys = [
        load_keyring(layout.credential(target), target).active.material
        for target in INITIAL_TARGETS
        if target not in {"token_public", "metrics"}
    ]
    keys += [
        load_keyring(layout.handoff(target), "token_private").active.material
        for target in HANDOFF_TARGETS
    ]
    assert len(set(keys)) == len(keys)
    private = load_keyring(layout.credential("token_private"), "token_private")
    public = load_keyring(layout.credential("token_public"), "token_public")
    assert public.active.material == private.public().active.material
    assert "key" not in root.yaml_text()


@pytest.mark.parametrize("boundary", ["journal", "credential", "manifest"])
def test_pre_migration_resumes_interrupted_publication(tmp_path, monkeypatch, boundary):
    """Retry uses committed candidate bytes, never generates replacement keys."""
    configuration, identity = bootstrap_fixture(tmp_path)
    write = bootstrap.write_private
    failed = False

    def interrupt(path, value, **kwargs):
        """Simulate a crash just after the relevant durable atomic write."""
        nonlocal failed
        write(path, value, **kwargs)
        wanted = ".bootstrap-candidate" if boundary == "journal" else "credential"
        if not failed and path.name == wanted:
            failed = True
            raise OSError("simulated crash")

    def reject_select(self, version):
        """Keep complete version/keys but simulate loss before active selection."""
        raise OSError("simulated crash")

    with monkeypatch.context() as patch:
        if boundary == "manifest":
            patch.setattr(AuthorityStore, "select", reject_select)
        else:
            patch.setattr(bootstrap, "write_private", interrupt)
        with pytest.raises(OSError, match="simulated"):
            provision_initial_files(configuration, identity)
    before = inventory(configuration)
    provision_initial_files(configuration, identity)
    after = inventory(configuration)
    assert all(after[path] == value for path, value in before.items())


@pytest.mark.parametrize("kind", ["identity", "adopt", "replace", "mode", "role"])
def test_mismatching_input_is_never_overwritten(tmp_path, kind):
    """The one-shot exception does not grant repair, rotation or account recovery."""
    configuration, identity = bootstrap_fixture(tmp_path)
    layout = RuntimeLayout(configuration)
    if kind != "adopt":
        provision_initial_files(configuration, identity)
    if kind == "identity":
        identity = BootstrapIdentity(uuid4(), "other@example.org")
    elif kind in {"adopt", "replace"}:
        write_private(layout.credential("django_signing"), b"unrelated-existing-key")
    elif kind == "mode":
        layout.credential_directory("metrics").chmod(0o755)
    elif kind == "role":
        configuration = replace(configuration, service_role=ServiceRole.WEB)
    before = inventory(configuration)
    with pytest.raises((ConfigError, ValueError)):
        provision_initial_files(configuration, identity)
    assert all(
        inventory(configuration)[path] == value for path, value in before.items()
    )


def test_running_service_prevents_every_bootstrap_write(tmp_path):
    """A real shared online lease excludes initial offline provisioning."""
    configuration, identity = bootstrap_fixture(tmp_path)
    before = inventory(configuration)
    with (
        StartupLease(RuntimeLayout(configuration).interlock, offline=False),
        pytest.raises(StartupBusy),
    ):
        provision_initial_files(configuration, identity)
    assert inventory(configuration) == before


def test_complete_marker_blocks_pre_migration_reuse(tmp_path):
    """A configured/restored marker cannot be recycled into first-time setup."""
    configuration, identity = bootstrap_fixture(tmp_path)
    layout = RuntimeLayout(configuration)
    provision_initial_files(configuration, identity)
    path = layout.deployment_directory / "bootstrap.json"
    marker = json.loads(read_private(path)) | {"state": "materialized"}
    write_private(path, json.dumps(marker).encode())
    before = inventory(configuration)
    with pytest.raises(ConfigError, match="already been initialized"):
        provision_initial_files(configuration, identity)
    assert inventory(configuration) == before


def test_invalid_operator_input_does_not_freeze_bootstrap_identity(tmp_path):
    """Input validation precedes intent publication and permits a corrected retry."""
    configuration, identity = bootstrap_fixture(tmp_path)
    layout = RuntimeLayout(configuration)
    write_private(configuration.secrets["google_oauth"], b"invalid-private-oauth")
    with pytest.raises(ConfigError):
        provision_initial_files(configuration, identity)
    assert not (layout.deployment_directory / "bootstrap.json").exists()
    write_private(
        configuration.secrets["google_oauth"],
        b'{"client_id":"fake","client_secret":"fake"}',
    )
    corrected = BootstrapIdentity(uuid4(), "corrected@example.org")
    assert provision_initial_files(configuration, corrected)
