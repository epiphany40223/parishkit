"""Operator-only health output includes fixed booleans, never exception values."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from parishkit.config import ConfigError
from parishkit.stewardship import runtime_diagnostics
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import ServiceRole
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.startup_interlock import StartupBusy, StartupLease

from .bootstrap_factory import bootstrap_fixture


@pytest.mark.parametrize("ready", [False, True])
def test_cli_reports_each_internal_check_and_correct_exit_code(
    ready, monkeypatch, capsys
):
    checks = {"database": True, "valkey": ready}
    marker = object()
    monkeypatch.setattr(runtime_diagnostics, "load_deployment", lambda path: marker)
    monkeypatch.setattr(runtime_diagnostics, "health_command", lambda config: checks)
    assert main(["health", "--config", "private-input.yaml"]) == (0 if ready else 1)
    result = capsys.readouterr()
    assert json.loads(result.out) == {"checks": checks, "ready": ready}
    assert result.err == ""


def test_cli_health_refuses_missing_config_and_private_errors(monkeypatch, capsys):
    """Host diagnosis cannot silently use different credentials or expose failures."""
    assert main(["health"]) == 2

    def fail(path):
        raise ValueError("private-error-value")

    monkeypatch.setattr(runtime_diagnostics, "load_deployment", fail)
    assert main(["health", "--config", "private-input"]) == 2
    output = capsys.readouterr()
    assert "private" not in output.out + output.err


def test_diagnostics_reports_offline_maintenance_without_private_detail(
    monkeypatch, capsys
):
    """A normal maintenance exclusion has a useful fixed operator diagnosis."""

    def fail(_path):
        raise StartupBusy("private path")

    monkeypatch.setattr(runtime_diagnostics, "load_deployment", fail)
    assert main(["health", "--config", "private-input"]) == 2
    output = capsys.readouterr()
    assert "offline maintenance" in output.err
    assert "private" not in output.err


@pytest.mark.parametrize("authority", [False, True])
def test_health_command_admission_lease_and_cleanup(tmp_path, monkeypatch, authority):
    """Only an admitted web profile may inspect dependencies under a real lease."""
    config, _ = bootstrap_fixture(tmp_path)
    config = replace(config, service_role=ServiceRole.WEB)
    events = []
    monkeypatch.setattr(
        "parishkit.stewardship.service_boundaries.admit_online_service",
        lambda value: value.service_role,
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_web.admit_lifecycle_mounts",
        lambda _: events.append("mounts"),
    )
    monkeypatch.setattr(
        "parishkit.stewardship.operator_commands.configure_operator_database",
        lambda _: events.append("database"),
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_web.valkey_client",
        lambda _: SimpleNamespace(
            connection_pool=SimpleNamespace(
                disconnect=lambda: events.append("broker_closed")
            )
        ),
    )
    monkeypatch.setattr(
        "django.db.connections.close_all", lambda: events.append("sql_closed")
    )
    from parishkit.stewardship.accounts.metrics_credentials import MetricsCredential

    config = replace(config, secrets={"metrics": tmp_path / "metrics"})
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.key_files.read_private",
        lambda _: MetricsCredential.generate().serialize(),
    )

    def checks():
        with (
            pytest.raises(StartupBusy),
            StartupLease(RuntimeLayout(config).interlock, offline=True),
        ):
            pass
        return {"database": True, "valkey": True}

    monkeypatch.setattr(
        "parishkit.stewardship.runtime_health.RuntimeHealth",
        lambda *args: SimpleNamespace(dependency_observation=checks),
    )

    def admit(_configuration):
        if not authority:
            raise ConfigError("private database detail")

    monkeypatch.setattr(
        "parishkit.stewardship.runtime_grants.admit_runtime_database", admit
    )
    with pytest.raises(ConfigError, match="web profile"):
        runtime_diagnostics.health_command(
            replace(config, service_role=ServiceRole.BOOTSTRAP)
        )
    assert events == []
    with (
        StartupLease(RuntimeLayout(config).interlock, offline=True),
        pytest.raises(StartupBusy),
    ):
        runtime_diagnostics.health_command(config)
    assert events == ["mounts"]
    result = runtime_diagnostics.health_command(config)
    assert result == {"database": True, "valkey": True, "database_authority": authority}
    assert events[-2:] == ["sql_closed", "broker_closed"]
    with StartupLease(RuntimeLayout(config).interlock, offline=True):
        pass
