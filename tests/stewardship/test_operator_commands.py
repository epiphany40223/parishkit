"""Offline CLI requires explicit intent and never reflects sensitive failures."""

from types import SimpleNamespace

import pytest

from parishkit.stewardship import operator_commands
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import ServiceRole


@pytest.mark.parametrize("command", ["bootstrap", "migrate", "recover-admin"])
def test_offline_commands_require_config_and_refuse_unmounted_host(command, capsys):
    """A host invocation or configured role label alone grants no operator profile."""
    assert main([command]) == 2
    result = capsys.readouterr()
    assert result.out == ""
    assert "offline operation refused" in result.err


def test_cli_forwards_only_explicit_bootstrap_parameters(monkeypatch, tmp_path, capsys):
    """The syntax boundary preserves typed intent without weakening kernel checks."""
    marker = object()
    monkeypatch.setattr(operator_commands, "load_deployment", lambda path: marker)
    calls = []

    def bootstrap(config, **parameters):
        """Isolate dispatch here; real offline mount and process tests are separate."""
        calls.append((config, parameters))
        return {"completed": True}

    monkeypatch.setattr(operator_commands, "bootstrap_command", bootstrap)
    assert (
        main(
            [
                "bootstrap",
                "--config",
                str(tmp_path / "input.yaml"),
                "--phase",
                "prepare",
                "--deployment-id",
                "00000000-0000-0000-0000-000000000001",
                "--admin-email",
                "admin@example.org",
            ]
        )
        == 0
    )
    assert calls == [
        (
            marker,
            {
                "phase": "prepare",
                "deployment_id": "00000000-0000-0000-0000-000000000001",
                "admin_email": "admin@example.org",
            },
        )
    ]
    assert "admin@example.org" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "error", [ValueError("private-password"), OSError("private-path")]
)
def test_operator_failures_do_not_print_exception_values(
    monkeypatch, tmp_path, capsys, error
):
    """Private-value sanitization includes errors raised before Django startup."""

    def fail(path):
        raise error

    monkeypatch.setattr(operator_commands, "load_deployment", fail)
    assert main(["migrate", "--config", str(tmp_path / "private-path")]) == 2
    output = capsys.readouterr()
    assert "private" not in output.out + output.err


@pytest.mark.parametrize("value", [None, "private-token", True, "0" * 32])
def test_operator_confirmations_require_canonical_uuid(value):
    with pytest.raises(ValueError):
        operator_commands._uuid(value)


def test_bootstrap_cannot_run_as_an_online_service(monkeypatch):
    """Operator dispatch repeats the specific role check after kernel admission."""
    monkeypatch.setattr(
        operator_commands, "admit_offline_service", lambda config: ServiceRole.WEB
    )
    with pytest.raises(ValueError):
        operator_commands.bootstrap_command(
            SimpleNamespace(),
            phase="prepare",
            deployment_id="00000000-0000-0000-0000-000000000001",
            admin_email="admin@example.org",
        )


@pytest.mark.parametrize(
    "case", ["wrong-login", "superuser", "configured", "missing-policy", "valid"]
)
def test_migration_safety_branches_precede_success(tmp_path, monkeypatch, case):
    """Fast refusal-path evidence complements actual CLI/SQL Compose migration tests."""
    from dataclasses import replace
    from unittest.mock import MagicMock

    from parishkit.config import ConfigError

    from .bootstrap_factory import bootstrap_fixture

    configuration, _ = bootstrap_fixture(tmp_path)
    configuration = replace(configuration, service_role=ServiceRole.MIGRATION)
    monkeypatch.setattr(
        operator_commands, "admit_offline_service", lambda _: ServiceRole.MIGRATION
    )
    monkeypatch.setattr(
        operator_commands, "configure_operator_database", lambda _: None
    )
    identity = ["pk_stewardship_migration"] * 2 + [False] * 6
    if case == "wrong-login":
        identity[0] = "pk_stewardship_web"
    if case == "superuser":
        identity[2] = True
    rows = [
        tuple(identity),
        ("system_configuration",),
        (case == "configured",),
        None if case == "missing-policy" else (4,),
    ]
    database = MagicMock()
    database.cursor.return_value.__enter__.return_value.fetchone.side_effect = rows
    monkeypatch.setattr("django.db.connection", database)
    loader = MagicMock()
    loader.applied_migrations = loader.disk_migrations = {}
    monkeypatch.setattr("django.db.migrations.loader.MigrationLoader", lambda _: loader)
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_database.require_current_schema", lambda: None
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_database.require_role_capacity", lambda _: None
    )
    migrate = MagicMock()
    monkeypatch.setattr("django.core.management.call_command", migrate)
    if case == "valid":
        assert operator_commands.migrate_command(configuration) == {
            "migrations_current": True
        }
    else:
        with pytest.raises(ConfigError):
            operator_commands.migrate_command(configuration)
    assert migrate.call_count == int(case in {"missing-policy", "valid"})
