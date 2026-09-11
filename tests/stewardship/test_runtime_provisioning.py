"""Fresh storage creation is resumable without adopting data or changing secrets."""

import json
from dataclasses import replace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import runtime_provisioning as provisioning
from parishkit.stewardship.accounts.key_files import read_private, write_private
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import load_deployment
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.startup_interlock import MARKER

IMAGE = "parishkit-stewardship:development"


def configuration_at(root):
    """Minimal operator input contains no credentials and opens no database."""
    return load_deployment(environ={"PARISHKIT_ROOT": str(root)})


def test_fresh_provisioning_creates_narrow_files_but_no_provider_or_app_secrets(
    tmp_path,
):
    """Only SQL/Valkey passwords and metadata precede the separate bootstrap phase."""
    root = tmp_path / "runtime"
    configuration = configuration_at(root)
    result = provisioning.provision_runtime(configuration, image=IMAGE)
    assert result["runtime_storage_provisioned"] is True
    assert result["services_started"] is False
    layout = RuntimeLayout(configuration)
    assert read_private(layout.interlock) == MARKER
    assert len(read_private(layout.database_password("web"))) == 43
    assert read_private(layout.database_password("web")) != read_private(
        layout.database_password("operator")
    )
    assert not layout.credential("google_oauth").exists()
    assert not layout.credential("metrics").exists()
    assert not layout.credential("token_private").exists()
    compose = json.loads(read_private(layout.service_directory / "compose.json"))
    assert "web" in compose["services"]
    for path in root.rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    with pytest.raises(ConfigError, match="already provisioned"):
        provisioning.provision_runtime(configuration, image=IMAGE)


def test_interrupted_provisioning_keeps_passwords_and_requires_exact_intent(
    tmp_path, monkeypatch
):
    """A retry reuses complete password files and refuses unrelated metadata edits."""
    root = tmp_path / "runtime"
    configuration = configuration_at(root)
    original = provisioning._retain

    def interrupt(path, value):
        """Fail after password generation, before the first derived ACL document."""
        raise OSError("synthetic private path")

    monkeypatch.setattr(provisioning, "_retain", interrupt)
    with pytest.raises(OSError):
        provisioning.provision_runtime(configuration, image=IMAGE)
    layout = RuntimeLayout(configuration)
    before = read_private(layout.database_password("web"))
    with pytest.raises(ConfigError, match="different deployment inputs"):
        provisioning.provision_runtime(
            replace(configuration, public_origin="http://localhost:9999"), image=IMAGE
        )
    monkeypatch.setattr(provisioning, "_retain", original)
    assert provisioning.provision_runtime(configuration, image=IMAGE)[
        "runtime_storage_provisioned"
    ]
    assert read_private(layout.database_password("web")) == before


def test_interrupted_provisioning_does_not_overwrite_changed_artifact(
    tmp_path, monkeypatch
):
    """A different existing metadata file is a mismatch, not an invitation to repair."""
    root = tmp_path / "runtime"
    configuration = configuration_at(root)
    original = provisioning._retain

    def interrupt(path, value):
        """Leave generated documents but no completion marker."""
        if path.name == ".stewardship-provisioned.json":
            raise OSError("interrupt")
        return original(path, value)

    monkeypatch.setattr(provisioning, "_retain", interrupt)
    with pytest.raises(OSError):
        provisioning.provision_runtime(configuration, image=IMAGE)
    target = RuntimeLayout(configuration).service_directory / "web.yaml"
    write_private(target, b"changed operator document")
    monkeypatch.setattr(provisioning, "_retain", original)
    with pytest.raises(ConfigError, match="differs"):
        provisioning.provision_runtime(configuration, image=IMAGE)
    assert read_private(target) == b"changed operator document"


@pytest.mark.parametrize("kind", ["populated", "public", "symlink", "file"])
def test_initial_provisioning_refuses_existing_unsafe_root(tmp_path, kind):
    """No chmod, chown, recursive deletion or adoption of an existing tree occurs."""
    root = tmp_path / "runtime"
    if kind == "file":
        root.write_bytes(b"preserve")
    elif kind == "symlink":
        root.symlink_to(tmp_path, target_is_directory=True)
    else:
        root.mkdir(mode=0o755 if kind == "public" else 0o700)
        if kind == "populated":
            (root / "preserve").write_bytes(b"preserve")
    with pytest.raises(ConfigError):
        provisioning.provision_runtime(configuration_at(root), image=IMAGE)
    assert not (root / ".stewardship-provisioning.json").exists()
    if kind == "populated":
        assert (root / "preserve").read_bytes() == b"preserve"


def test_external_overrides_are_created_only_when_empty_and_safe(tmp_path):
    """Explicit independent targets need not live below the default runtime root."""
    root = tmp_path / "runtime"
    configuration = configuration_at(root)
    selected = tmp_path / "external-passwords"
    configuration = replace(
        configuration,
        postgres=replace(
            configuration.postgres, password_files={"web": selected / "web"}
        ),
    )
    assert provisioning.provision_runtime(configuration, image=IMAGE)[
        "runtime_storage_provisioned"
    ]
    assert len(read_private(selected / "web")) == 43


def test_populated_external_override_refuses_before_creating_root(tmp_path):
    """The preflight inspects all destinations before claiming the fresh root."""
    selected = tmp_path / "external-passwords"
    selected.mkdir(mode=0o700)
    write_private(selected / "preserve", b"existing-user-data")
    root = tmp_path / "runtime"
    configuration = configuration_at(root)
    configuration = replace(
        configuration,
        postgres=replace(
            configuration.postgres, password_files={"web": selected / "web"}
        ),
    )
    with pytest.raises(ConfigError, match="must be empty"):
        provisioning.provision_runtime(configuration, image=IMAGE)
    assert not root.exists()
    assert read_private(selected / "preserve") == b"existing-user-data"


def test_provisioning_cli_never_echoes_private_error_or_input(capsys):
    """No default target, traceback or raw configuration error reaches the console."""
    assert main(["provision-runtime", "--config", "private-input"]) == 2
    captured = capsys.readouterr()
    assert "private-input" not in captured.out + captured.err
    assert "owner-only targets" in captured.err
