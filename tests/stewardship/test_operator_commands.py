"""Offline CLI requires explicit intent and never reflects sensitive failures."""

from types import SimpleNamespace

import pytest

from parishkit.stewardship import operator_commands
from parishkit.stewardship.cli import main
from parishkit.stewardship.deployment import ServiceRole


@pytest.mark.parametrize(
    "command",
    [
        "bootstrap",
        "migrate",
        "recover-admin",
        "preview-admin-recovery",
        "database-roles",
        "database-grants",
    ],
)
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
    "command",
    ["database-roles", "database-grants", "preview-admin-recovery", "recover-admin"],
)
def test_remaining_operator_dispatch_is_explicit_and_profile_scoped(
    tmp_path, monkeypatch, command
):
    """All operator branches route confirmed inputs through their named owner."""
    from dataclasses import replace
    from unittest.mock import Mock
    from uuid import uuid4

    from .bootstrap_factory import bootstrap_fixture

    configuration, _ = bootstrap_fixture(tmp_path)
    configuration = replace(configuration, service_role=ServiceRole.DATABASE_PROVISION)
    monkeypatch.setattr(operator_commands, "load_deployment", lambda _: configuration)
    monkeypatch.setattr(
        operator_commands, "admit_offline_service", lambda _: configuration.service_role
    )
    monkeypatch.setattr(
        operator_commands, "configure_operator_database", lambda _: None
    )
    procedure = Mock(return_value={"verified": True})
    name = {
        "database-roles": "database_provisioning.provision_roles",
        "database-grants": "database_provisioning.provision_grants",
        "preview-admin-recovery": "operator_commands.preview_recovery_command",
        "recover-admin": "operator_commands.recover_admin_command",
    }[command]
    monkeypatch.setattr("parishkit.stewardship." + name, procedure)
    identifier = str(uuid4())
    args = [command, "--config", "synthetic", "--confirm-deployment", identifier]
    if "recovery" in command or command == "recover-admin":
        args += ["--target-email", "admin@example.org"]
    if command == "recover-admin":
        args += [
            "--operation-id",
            str(uuid4()),
            "--confirm-email",
            "admin@example.org",
            "--operator-name",
            "Operator",
            "--reason",
            "Synthetic test",
        ]
    assert main(args) == 0
    procedure.assert_called_once()
    assert procedure.call_args.args[0] is configuration


def test_recovery_wrappers_refuse_wrong_identity_and_nonapplied_receipt(
    tmp_path, monkeypatch
):
    """A successful function return alone cannot claim that recovery was applied."""
    from uuid import uuid4

    from parishkit.config import ConfigError

    from .bootstrap_factory import bootstrap_fixture

    configuration, _ = bootstrap_fixture(tmp_path)
    values = dict(
        deployment_id=str(uuid4()),
        operation_id=str(uuid4()),
        target_email="admin@example.org",
        confirmed_email="admin@example.org",
        operator_name="Operator",
        reason="Test",
    )
    monkeypatch.setattr(
        operator_commands, "admit_offline_service", lambda _: ServiceRole.WEB
    )
    with pytest.raises(ConfigError, match="operator profile"):
        operator_commands.recover_admin_command(configuration, **values)
    with pytest.raises(ConfigError, match="operator profile"):
        operator_commands.preview_recovery_command(
            configuration,
            deployment_id=values["deployment_id"],
            target_email=values["target_email"],
        )
    monkeypatch.setattr(
        operator_commands, "admit_offline_service", lambda _: ServiceRole.ADMIN_RECOVERY
    )
    monkeypatch.setattr(
        operator_commands, "configure_operator_database", lambda _: None
    )
    monkeypatch.setattr(
        "parishkit.stewardship.runtime_database.admit_offline_database", lambda _: None
    )
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.operator_recovery.recover_admin",
        lambda *args, **kwargs: SimpleNamespace(state="failed"),
    )
    with pytest.raises(ConfigError, match="applied durable receipt"):
        operator_commands.recover_admin_command(configuration, **values)
    monkeypatch.setattr(
        "parishkit.stewardship.accounts.configuration_installation.coherent_configuration",
        lambda _: SimpleNamespace(pk=uuid4()),
    )
    with pytest.raises(ConfigError, match="deployment does not match"):
        operator_commands.preview_recovery_command(
            configuration,
            deployment_id=values["deployment_id"],
            target_email=values["target_email"],
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
