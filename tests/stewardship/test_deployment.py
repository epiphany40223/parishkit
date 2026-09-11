"""ARC-02 deployment input precedence, shape validation, and path contracts."""

import os
from pathlib import Path

import pytest
import yaml

from parishkit import config as shared_config
from parishkit.config import ConfigError
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import (
    PATH_DEFAULTS,
    PERSISTENT_STORES,
    DeploymentProfile,
    ServiceRole,
    load_deployment,
)

EXAMPLES = tuple(
    sorted(Path(__file__).resolve().parents[2].glob("scripts/*/example-config.yaml"))
)


def test_shipped_example_set_cannot_disappear():
    """A missing example tree must fail instead of yielding empty parametrizations."""
    assert len(EXAMPLES) >= 8, "shipped example coverage is incomplete"


def config_file(tmp_path, deployment):
    """Write only synthetic deployment metadata to a temporary test YAML file."""
    path = tmp_path / "deployment.yaml"
    path.write_text(yaml.safe_dump({"deployment": deployment}), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "example",
    EXAMPLES,
    ids=lambda path: path.parent.name,
)
def test_shipped_examples_validate_without_modification(example):
    """Validate the actual copied deployment example, not only synthetic sections."""
    config = load_deployment(example, environ={})
    assert config.profile is DeploymentProfile.DEVELOPMENT
    assert config.public_origin.startswith("http://localhost:")


@pytest.mark.parametrize("error", [RuntimeError, ValueError, OSError])
def test_deployment_path_expansion_failure_is_sanitized(error, monkeypatch, capsys):
    """Path resolution failures become configuration errors at the owning boundary."""
    original = Path.expanduser

    def fail_private_path(path):
        """Inject only into the runtime override, leaving config-file reads intact."""
        if str(path).startswith("~unknown"):
            raise error("synthetic-private-home")
        return original(path)

    monkeypatch.setattr(Path, "expanduser", fail_private_path)
    with pytest.raises(ConfigError, match="path cannot be resolved") as exc:
        load_deployment(environ={"PARISHKIT_ROOT": "~unknown/private"})
    assert "private" not in str(exc.value)
    assert main(["validate-deployment", "--runtime-root", "~unknown/private"]) == 2
    assert capsys.readouterr() == ("", "ERROR: deployment configuration is invalid\n")


@pytest.mark.parametrize(
    "example",
    EXAMPLES,
    ids=lambda path: path.parent.name,
)
@pytest.mark.parametrize("explicit_deployment", [False, True])
def test_shared_example_sections_are_accepted(tmp_path, example, explicit_deployment):
    """Every tool's documented sections coexist with optional deployment input."""
    document = yaml.safe_load(example.read_text(encoding="utf-8"))
    document.pop("deployment", None)
    if explicit_deployment:
        document["deployment"] = {"public_origin": "http://localhost:9005"}
    path = tmp_path / "shared.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    config = load_deployment(path, environ={})
    assert config.profile is DeploymentProfile.DEVELOPMENT
    assert config.public_origin == (
        "http://localhost:9005" if explicit_deployment else "http://localhost:8000"
    )
    assert not config.secrets
    assert config.postgres.password_file is None


def test_other_section_contents_are_not_deployment_settings(tmp_path):
    """Only the owning tool interprets fields beneath a recognized section."""
    path = tmp_path / "shared.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "google": {
                    "profile": "production",
                    "public_origin": "synthetic-secret",
                },
                "jobs": [{"command": "unused-command"}],
                "deployment": {"public_origin": "http://localhost:9005"},
            }
        ),
        encoding="utf-8",
    )
    config = load_deployment(path, environ={})
    assert config.profile is DeploymentProfile.DEVELOPMENT
    assert config.public_origin == "http://localhost:9005"


@pytest.mark.parametrize("typo", ["deploymnet", "gooogle", "unknown"])
@pytest.mark.parametrize("explicit_deployment", [False, True])
def test_unknown_top_level_sections_still_fail(tmp_path, typo, explicit_deployment):
    """Expanded recognition must not silently accept misspelled section names."""
    document = {typo: {"synthetic-secret": "synthetic-secret"}, "google": {}}
    if explicit_deployment:
        document["deployment"] = {"public_origin": "http://localhost:9005"}
    path = tmp_path / "shared.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(
        ConfigError, match="invalid top-level configuration shape"
    ) as exc:
        load_deployment(path, environ={})
    assert "synthetic-secret" not in str(exc.value)


def test_development_defaults_do_not_touch_storage(tmp_path):
    """Defaults are late-bound, rooted, immutable, and free of credential reads."""
    root = tmp_path / "does-not-exist"
    config = load_deployment(environ={"PARISHKIT_ROOT": str(root)})
    assert config.profile is DeploymentProfile.DEVELOPMENT
    assert config.service_role is ServiceRole.WEB
    assert config.public_origin == "http://localhost:8000"
    assert config.trusted_proxy_hops == 0
    assert config.paths.root == root
    for name in PATH_DEFAULTS:
        assert config.paths[name] == root / name
    assert config.paths["authority"] == root / "config/stewardship"
    assert config.paths["persistent_root"] == root / "run/persistent"
    for name in PERSISTENT_STORES:
        assert config.paths[name] == root / "run/persistent" / name
    assert config.postgres.port == 5432
    assert config.valkey.port == 6379
    assert not config.secrets and config.postgres.password_file is None
    assert not root.exists()
    with pytest.raises(TypeError):
        config.paths.values["run"] = Path("/another")
    with pytest.raises(TypeError):
        config.secrets["token_private"] = Path("/another")
    assert load_deployment(environ={}).paths.root == Path("/opt/parishkit")


def test_precedence_and_relative_paths(tmp_path, monkeypatch):
    """CLI overrides beat environment and YAML; path bases follow their source."""
    source = tmp_path / "config-dir"
    source.mkdir()
    path = config_file(
        source,
        {
            "public_origin": "http://localhost:9000",
            "paths": {
                "root": "yaml-root",
                "reports": "private-reports",
                "run": "runtime",
            },
            "postgres": {"host": "yaml-postgres", "password_file": "db-password"},
        },
    )
    monkeypatch.chdir(tmp_path)
    config = load_deployment(
        path,
        environ={
            "PARISHKIT_ROOT": "env-root",
            "PARISHKIT_STEWARDSHIP_PUBLIC_ORIGIN": "http://localhost:9001",
            "PARISHKIT_STEWARDSHIP_POSTGRES_HOST": "env-postgres",
        },
        overrides={
            "PARISHKIT_ROOT": "cli-root",
            "PARISHKIT_STEWARDSHIP_PUBLIC_ORIGIN": "http://localhost:9002",
        },
    )
    assert config.paths.root == tmp_path / "cli-root"
    assert config.paths["reports"] == source / "private-reports"
    assert config.paths["postgresql"] == source / "runtime/persistent/postgresql"
    assert config.postgres.password_file == source / "db-password"
    assert config.postgres.host == "env-postgres"
    assert config.public_origin == "http://localhost:9002"
    assert load_deployment(path, environ={}).paths.root == source / "yaml-root"


@pytest.mark.parametrize(
    "name", ["web", "backup-worker", "credential-installer-general-encryption"]
)
def test_named_database_file_overrides_have_closed_names_and_source_relative_paths(
    tmp_path, monkeypatch, name
):
    """Each SQL identity can override its own file through YAML and environment."""
    source = tmp_path / "source"
    source.mkdir()
    path = config_file(source, {"postgres": {"password_files": {name: "web-password"}}})
    monkeypatch.chdir(tmp_path)
    parsed = load_deployment(path, environ={})
    assert parsed.configuration_file == path
    assert parsed.postgres.password_files == {name: source / "web-password"}
    variable = "PARISHKIT_STEWARDSHIP_POSTGRES_PASSWORD_FILE_" + name.upper().replace(
        "-", "_"
    )
    parsed = load_deployment(path, environ={variable: "env-web"})
    assert parsed.postgres.password_files == {name: tmp_path / "env-web"}
    invalid = config_file(
        source, {"postgres": {"password_files": {"unexpected": "secret"}}}
    )
    with pytest.raises(ConfigError):
        load_deployment(invalid, environ={})


@pytest.mark.parametrize(
    "name", [*PATH_DEFAULTS, "authority", "persistent_root", *sorted(PERSISTENT_STORES)]
)
def test_every_path_can_be_overridden(tmp_path, name):
    """Individual stores remain independently movable despite a global root."""
    config = load_deployment(
        environ={f"PARISHKIT_STEWARDSHIP_PATH_{name.upper()}": str(tmp_path / name)}
    )
    assert config.paths[name] == tmp_path / name


def test_production_requires_explicit_origin(tmp_path):
    """Parsing accepts safe metadata but never claims runtime readiness."""
    with pytest.raises(ConfigError):
        load_deployment(environ={"PARISHKIT_STEWARDSHIP_PROFILE": "production"})
    path = config_file(
        tmp_path, {"profile": "production", "public_origin": "https://parish.example/"}
    )
    config = load_deployment(path, environ={})
    assert config.profile is DeploymentProfile.PRODUCTION
    assert config.public_origin == "https://parish.example"
    assert config.trusted_proxy_hops == 1


@pytest.mark.parametrize(
    "origin",
    [
        "https://user:synthetic-secret@parish.example",
        "https://parish.example/path",
        "https://parish.example?token=synthetic-secret",
        "https://parish.example#fragment",
        "https://[broken",
        "https://parish.example:65536",
        "https://parish.example:",
        "https://parish.example\n.attacker.example",
        "https://parish.example\\attacker",
        "https://bad_host.example",
        "https://" + "x" * 64 + ".example",
        "http://parish.example",
        "file:///secret",
        "",
        42,
    ],
)
def test_invalid_production_origins(tmp_path, origin):
    """Credentials and non-origin URL components are neither accepted nor echoed."""
    path = config_file(tmp_path, {"profile": "production", "public_origin": origin})
    with pytest.raises(ConfigError) as exc:
        load_deployment(path, environ={})
    assert "synthetic-secret" not in str(exc.value)


@pytest.mark.parametrize(
    "origin", ["http://0.0.0.0:8000", "http://parish.example", "https://localhost"]
)
def test_local_origins_are_loopback_http(tmp_path, origin):
    """Local development cannot accidentally advertise public HTTP deployment."""
    with pytest.raises(ConfigError):
        load_deployment(config_file(tmp_path, {"public_origin": origin}), environ={})


@pytest.mark.parametrize(
    "deployment",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"profile": "unknown"},
        {"service_role": "unknown"},
        {"secret-password": "sensitive"},
        {"postgres": {"port": True}},
        {"postgres": {"port": 0}},
        {"postgres": {"host": "user:sensitive@host"}},
        {"valkey": {"host": "https://host"}},
        {"postgres": {"connect_timeout": 61}},
        {"valkey": {"database": 16}},
        {"secrets": {"unknown": "secret"}},
        {"trusted_proxy_hops": 1},
        {"paths": {"reports": ""}},
        {"postgres": {"host": " "}},
        {"postgres": {"password": "sensitive"}},
        {"paths": []},
        {"credential_target": "google_oauth"},
        {"service_role": "credential-installer"},
        {"service_role": "credential-installer", "credential_target": "unknown"},
        {"service_role": "credential-installer", "credential_target": ["google_oauth"]},
        {
            "profile": "production",
            "public_origin": "https://parish.example",
            "trusted_proxy_hops": 0,
        },
    ],
)
def test_invalid_deployment_shape(tmp_path, deployment):
    """Unknown values, secret contents, and unsafe profile combinations fail."""
    with pytest.raises(ConfigError) as exc:
        load_deployment(config_file(tmp_path, deployment), environ={})
    assert "sensitive" not in str(exc.value)


def test_secret_references_are_paths_only(tmp_path):
    """Parsing cannot read key bytes or accidentally include file paths in repr."""
    config = load_deployment(
        config_file(
            tmp_path,
            {
                "service_role": "credential-installer",
                "credential_target": "google_oauth",
                "secrets": {"handoff_private": "sensitive-path"},
            },
        ),
        environ={},
    )
    assert config.secrets["handoff_private"] == tmp_path / "sensitive-path"
    assert "sensitive-path" not in repr(config)


def test_numeric_environment_and_ipv6_host():
    """Environment strings resolve to typed values and literal IPs are supported."""
    config = load_deployment(
        environ={
            "PARISHKIT_STEWARDSHIP_POSTGRES_PORT": "5433",
            "PARISHKIT_STEWARDSHIP_POSTGRES_HOST": "::1",
            "PARISHKIT_STEWARDSHIP_VALKEY_DATABASE": "2",
            "PARISHKIT_STEWARDSHIP_PUBLIC_ORIGIN": "http://[::1]:8000",
        }
    )
    assert config.postgres.port == 5433 and config.postgres.host == "::1"
    assert config.valkey.database == 2


def test_unknown_environment_override_fails_closed():
    """A misspelled explicit security setting must not silently use defaults."""
    with pytest.raises(ConfigError):
        load_deployment(environ={"PARISHKIT_STEWARDSHIP_PROFIEL": "production"})
    with pytest.raises(ConfigError):
        load_deployment(environ={}, overrides={"unknown": "synthetic-secret"})


def test_yaml_errors_are_sanitized(tmp_path):
    """Parser failures cannot expose secrets through nested source-line errors."""
    path = tmp_path / "deployment.yaml"
    for text in (
        "secret: [synthetic-secret",
        "- synthetic-secret",
        "unknown: synthetic-secret",
    ):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigError) as exc:
            load_deployment(path, environ={})
        assert "synthetic-secret" not in str(exc.value)


@pytest.mark.parametrize("fault", ["depth", "nodes", "bytes", "recursion"])
def test_yaml_resource_failures_are_sanitized_at_deployment_and_cli(
    tmp_path, monkeypatch, capsys, fault
):
    """Strict read failures retain both deployment and CLI redaction contracts."""
    path = tmp_path / "synthetic-sensitive-path.yaml"
    text = "deployment: {public_origin: 'http://localhost:9005'}"
    if fault == "depth":
        text = "".join("  " * depth + "key:\n" for depth in range(550))
    elif fault == "nodes":
        monkeypatch.setattr(shared_config, "STRICT_YAML_MAX_NODES", 4)
    elif fault == "bytes":
        monkeypatch.setattr(shared_config, "STRICT_YAML_MAX_BYTES", 16)
    else:

        def fail(*args, **kwargs):
            """Simulate an unexpected recursion failure inside the YAML library."""
            raise RecursionError("synthetic-secret")

        monkeypatch.setattr(shared_config.yaml, "load", fail)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="^deployment YAML is unreadable or invalid$"):
        load_deployment(path, environ={})
    for command, error in (
        ("validate-deployment", "ERROR: deployment configuration is invalid\n"),
        ("config-check", "ERROR: configuration must be a readable YAML mapping\n"),
    ):
        assert main([command, "--config", str(path)]) == 2
        assert capsys.readouterr() == ("", error)


def test_cli_deployment_validation_is_not_readiness(capsys, monkeypatch):
    """The CLI validates metadata without claiming that service checks ran."""
    # Unlike direct loader tests, CLI dispatch reads the process environment.
    # Local operator settings must not change this synthetic scenario.
    for name in os.environ:
        if name == "PARISHKIT_ROOT" or name.startswith("PARISHKIT_STEWARDSHIP_"):
            monkeypatch.delenv(name)
    assert main(["validate-deployment", "--profile", "development"]) == 0
    assert '"startup_validated": false' in capsys.readouterr().out
    assert main(["validate-deployment", "--profile", "production"]) == 2
    assert capsys.readouterr().err == "ERROR: deployment configuration is invalid\n"
